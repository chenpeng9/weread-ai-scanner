import streamlit as st
import requests
import json
import time
import re
import pandas as pd
import random
import xml.etree.ElementTree as ET
import io
import os
from datetime import datetime, timedelta
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 1. 全局配置
# ==========================================
PAGE_TITLE = "WeRead AI (微读精选)"
PAGE_ICON = "📖"

# 🔒 必须严格对应 Google Sheets 的 Sheet 名称
SHEET_HISTORY = "History"
SHEET_ACCOUNTS = "Accounts"

EXPECTED_COLS = ["日期", "时间", "公众号", "标题", "价值", "摘要", "点评", "原文"]
ACCOUNT_COLS = ["ID", "公众号", "启用"]

SYSTEM_INSTRUCTION = """
【角色】你是一位像“浑水调研”一样毒辣、冷血的顶级做空机构分析师。你对市场噪音极度不耐烦，对“割韭菜”的行为深恶痛绝。
【评分标准 (0-10分)】
* 0-2分 (垃圾/收割)：任何带货、卖课、团购、广告软文、单纯的情绪宣泄。
* 3-5分 (平庸)：只有新闻罗列没有观点。
* 6-7分 (合格)：有基本的数据支撑和逻辑推演。
* 8-10分 (Alpha)：极其稀缺的行业内幕、深度的宏观推演。
【输出要求】
1. 摘要：80字内。
2. 点评：25字内毒舌点评。
3. 必须输出纯 JSON，key 为 "summary", "score", "suggestion"。
"""

# ==========================================
# 2. 数据层 (指名道姓版)
# ==========================================
class DataManager:
    def __init__(self):
        try:
            self.conn = st.connection("gsheets", type=GSheetsConnection)
            self.enabled = True
        except: self.enabled = False
            
    # --- 历史记录 (读 History 表) ---
    def load_history(self):
        if not self.enabled: return pd.DataFrame(columns=EXPECTED_COLS)
        try:
            # 🟢 显式指定 worksheet="History"
            df = self.conn.read(worksheet=SHEET_HISTORY, ttl=0)
            if df.empty or '日期' not in df.columns:
                return pd.DataFrame(columns=EXPECTED_COLS)
            
            for col in EXPECTED_COLS:
                if col not in df.columns: df[col] = ""
            df = df[EXPECTED_COLS]
            
            df['价值'] = pd.to_numeric(df['价值'], errors='coerce').fillna(0).astype(int)
            df['点评'] = df['点评'].fillna("").astype(str).replace("None", "").replace("nan", "")
            df['原文'] = df['原文'].fillna("").astype(str)
            df['日期'] = df['日期'].astype(str)
            df['标题'] = df['标题'].fillna("").astype(str)
            return df
        except Exception as e:
            # 如果找不到 History 表，不用 panic，返回空即可
            return pd.DataFrame(columns=EXPECTED_COLS)

    def save_history(self, new_df):
        if not self.enabled: return new_df
        try:
            new_df = new_df[EXPECTED_COLS]
            old = self.load_history()
            
            # 铁三角去重
            combined = pd.concat([old, new_df], ignore_index=True).drop_duplicates(
                subset=['公众号', '标题', '日期'], keep='first'
            )
            combined = combined.sort_values(by=["日期", "时间"], ascending=False)
            
            # 🟢 写入 History 表
            self.conn.update(worksheet=SHEET_HISTORY, data=combined)
            return combined
        except Exception as e:
            st.error(f"历史保存失败: {e}")
            return new_df

    def reset_history(self):
        if not self.enabled: return pd.DataFrame(columns=EXPECTED_COLS)
        try:
            empty = pd.DataFrame(columns=EXPECTED_COLS)
            self.conn.update(worksheet=SHEET_HISTORY, data=empty)
            return empty
        except: return pd.DataFrame(columns=EXPECTED_COLS)

    # --- 账号管理 (读 Accounts 表) ---
    def load_accounts(self):
        if not self.enabled: return pd.DataFrame(columns=ACCOUNT_COLS)
        try:
            # 🟢 显式指定 worksheet="Accounts"
            df = self.conn.read(worksheet=SHEET_ACCOUNTS, ttl=0)
            if df.empty: return pd.DataFrame(columns=ACCOUNT_COLS)
            
            for col in ACCOUNT_COLS:
                if col not in df.columns: df[col] = ""
            df = df[ACCOUNT_COLS]
            
            df['ID'] = df['ID'].astype(str)
            df['公众号'] = df['公众号'].astype(str)
            # 兼容布尔值转换
            df['启用'] = df['启用'].astype(str).map({'True': True, 'TRUE': True, '1': True, 'False': False, 'FALSE': False, '0': False}).fillna(True)
            return df
        except Exception as e:
            # 找不到 Accounts 表时返回空，界面会提示初始化
            return pd.DataFrame(columns=ACCOUNT_COLS)

    def save_accounts(self, df):
        if not self.enabled: return
        try:
            df = df[ACCOUNT_COLS]
            self.conn.update(worksheet=SHEET_ACCOUNTS, data=df)
            st.toast("✅ 账号配置已同步")
        except Exception as e:
            st.error(f"账号保存失败: {e}")

# ==========================================
# 3. 服务层
# ==========================================
class WxSource:
    def __init__(self, api_key, debug=False):
        self.key, self.debug = api_key, debug
        self.list_api = "http://data.wxrank.com/weixin/getps"
        self.content_api = "http://data.wxrank.com/weixin/artinfo"

    def get_scoped_articles(self, wxid, days=0):
        dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days + 1)]
        try:
            r = requests.get(self.list_api, params={"key": self.key, "wxid": wxid}, timeout=10).json()
            if self.debug and str(r.get("code")) != "0": st.error(f"⚠️ WxList Error: {r}")
            if str(r.get("code")) == "0":
                return [{"title": i.get("title") or i.get("msg_title"), 
                         "url": i.get("url") or i.get("art_url"), 
                         "date": (i.get("pub_time") or "")[:10], 
                         "full_time": i.get("pub_time") or ""} 
                        for i in (r.get("data", {}).get("list", []) or r.get("data", [])) 
                        if any((i.get("pub_time") or "").startswith(d) for d in dates)]
            return []
        except: return []

    def fetch_content(self, url):
        try:
            r = requests.post(self.content_api, json={"key": self.key, "url": url}, timeout=20).json()
            return r.get("data", {}).get("text", "")[:6000] if str(r.get("code")) == "0" else ""
        except: return ""

class AIAnalyst:
    def __init__(self, key, debug=False):
        self.key = key
        self.debug = debug
        self.models = ["gemini-2.0-flash", "gemini-2.5-flash"]

    def _get_url(self, model_name):
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.key}"

    def test_connection(self):
        try:
            r = requests.post(self._get_url(self.models[0]), json={"contents": [{"parts": [{"text": "Hello"}]}]}, timeout=10)
            return r.status_code, r.text
        except Exception as e: return 0, str(e)

    def _try_request(self, model, payload):
        try:
            r = requests.post(self._get_url(model), json=payload, timeout=30)
            if r.status_code == 200:
                raw = r.json()['candidates'][0]['content']['parts'][0]['text']
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                return json.loads(match.group(0)) if match else None
            return None
        except: return None

    def analyze(self, text, title):
        time.sleep(0.5)
        payload = {"system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]}, "contents": [{"parts": [{"text": f"分析《{title}》:\n{text}"}]}]}
        shuffled = self.models.copy(); random.shuffle(shuffled)
        for model in shuffled:
            if res := self._try_request(model, payload): return res
        return None

# ==========================================
# 4. 辅助函数
# ==========================================
def parse_xml(f):
    try:
        ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}
        return pd.DataFrame([
            {"ID": r.findall("ss:Cell", ns)[2].find("ss:Data", ns).text.strip(), 
             "公众号": r.findall("ss:Cell", ns)[1].find("ss:Data", ns).text.strip(), 
             "启用": True}
            for i, r in enumerate(ET.parse(f).getroot().findall(".//ss:Row", ns)) 
            if i > 0 and len(r.findall("ss:Cell", ns)) >= 3
        ])
    except: return None

def init_state():
    if 'data_manager' not in st.session_state: st.session_state.data_manager = DataManager()
    
    # 强制刷新
    if 'history_df' not in st.session_state: 
        st.session_state.history_df = st.session_state.data_manager.load_history()
    # 防撞
    if '日期' not in st.session_state.history_df.columns:
         st.session_state.history_df = pd.DataFrame(columns=EXPECTED_COLS)

    # 加载账号
    if 'config_df' not in st.session_state:
        st.session_state.config_df = st.session_state.data_manager.load_accounts()

# ==========================================
# 5. 界面渲染
# ==========================================
def render_sidebar():
    with st.sidebar:
        st.title(f"{PAGE_ICON} WeRead AI")
        if "WX_KEY" in st.secrets: wx_key = st.secrets["WX_KEY"]; st.success("✅ WxRank Key 已加载")
        else: wx_key = st.text_input("WxRank API Key")
        if "GEMINI_KEY" in st.secrets: gemini_key = st.secrets["GEMINI_KEY"]; st.success("✅ Gemini Key 已加载")
        else: gemini_key = st.text_input("Gemini API Key", type="password")
        
        st.divider()
        c1, c2 = st.columns(2)
        debug = c1.toggle("🐞 Debug", False)
        force = c2.toggle("⚡ 强刷", False)
        
        if debug and gemini_key and st.button("🧪 测试连接"):
            ana = AIAnalyst(gemini_key); code, msg = ana.test_connection()
            if code == 200: st.toast("✅ Gemini 通畅")
            else: st.error(f"失败: {msg}")

        time_scope = st.selectbox("📅 范围", [0, 1], format_func=lambda x: "仅今日" if x == 0 else "近48小时")
        
        st.divider()
        st.caption("☁️ 云端账号管理")
        
        # XML 导入
        with st.expander("📂 从 XML 导入"):
            if u := st.file_uploader("上传 XML", "xml"):
                if st.button("📥 确认导入覆盖"):
                    if (df_xml := parse_xml(u)) is not None:
                        st.session_state.data_manager.save_accounts(df_xml)
                        st.session_state.config_df = df_xml
                        st.rerun()

        # 表格编辑器
        if not st.session_state.config_df.empty:
            edited_df = st.data_editor(
                st.session_state.config_df,
                column_config={
                    "启用": st.column_config.CheckboxColumn("✅", width="small"),
                    "公众号": st.column_config.TextColumn("公众号", width="medium"),
                    "ID": st.column_config.TextColumn("ID", width="medium"),
                },
                num_rows="dynamic",
                use_container_width=True,
                key="account_editor"
            )
            if st.button("💾 保存配置到云端", type="primary", use_container_width=True):
                st.session_state.config_df = edited_df
                st.session_state.data_manager.save_accounts(edited_df)
                st.rerun()
        else:
            st.info("⚠️ 请新建 'Accounts' 表或导入数据")
            if st.button("➕ 初始化空表格"):
                init_df = pd.DataFrame([{"ID": "demo_id", "公众号": "示例账号", "启用": True}])
                st.session_state.data_manager.save_accounts(init_df)
                st.session_state.config_df = init_df
                st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        trigger = c1.button("🚀 开始", type="primary", use_container_width=True) 
        if c2.button("🗑️ 清空历史", use_container_width=True):
            st.session_state.history_df = st.session_state.data_manager.reset_history()
            st.rerun()
            
        return wx_key, gemini_key, time_scope, trigger, debug, force

def render_results():
    if st.session_state.history_df.empty or '日期' not in st.session_state.history_df.columns:
        st.info("👋 暂无记录，请点击侧边栏「🚀 开始」")
        return

    col1, col2 = st.columns([1.5, 1])
    with col1:
        raw_dates = st.session_state.history_df['日期'].astype(str).dropna().unique().tolist()
        all_dates = ["全部"] + sorted([d for d in raw_dates if len(d)>0], reverse=True)
        sel_date = st.selectbox("📅 日期回溯", all_dates, label_visibility="collapsed")
    with col2:
        show_table = st.toggle("📋 表格", False)

    df = st.session_state.history_df if sel_date == "全部" else st.session_state.history_df[st.session_state.history_df['日期'].astype(str) == sel_date]
    
    if show_table:
        def style_score(v):
            try:
                v = int(v)
                if v >= 8: return 'background-color: #d4edda; color: #155724; font-weight: bold'
                elif v >= 6: return 'background-color: #cce5ff; color: #004085'
                elif v >= 3: return 'background-color: #fff3cd; color: #856404'
                else: return 'background-color: #f8d7da; color: #721c24'
            except: return ''
        st.dataframe(
            df.sort_values(["日期", "时间"], ascending=False).style.map(style_score, subset=['价值']),
            column_config={
                "原文": st.column_config.LinkColumn("🔗", display_text="阅读"), 
                "价值": st.column_config.NumberColumn("分", format="%d"),
                "点评": st.column_config.TextColumn("点评", width="medium"),
                "摘要": st.column_config.TextColumn("摘要", width="large")
            },
            hide_index=True, use_container_width=True, height=600
        )
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("📥 导出Excel", b.getvalue(), f"WeRead_{datetime.now():%m%d}.xlsx", use_container_width=True)
    else:
        sort_mode = st.radio("排序", ["⏱️ 时间倒序", "🔥 评分最高"], horizontal=True, label_visibility="collapsed")
        df_sorted = df.sort_values(by=["价值", "日期", "时间"], ascending=[False, False, False]) if sort_mode == "🔥 评分最高" else df.sort_values(by=["日期", "时间"], ascending=[False, False])
        if df_sorted.empty: st.info("📭 无记录")
        for _, row in df_sorted.iterrows():
            try: score = int(row['价值'])
            except: score = 0
            if score >= 8: color, icon = "green", "🟢"
            elif score >= 6: color, icon = "blue", "🔵"
            elif score >= 3: color, icon = "orange", "🟡"
            else: color, icon = "red", "🔴"
            with st.container(border=True):
                c1, c2 = st.columns([3.5, 1])
                c1.markdown(f"**{row['标题']}**")
                c2.markdown(f":{color}[**{score}分**]")
                st.caption(f"💡 {row['摘要']}")
                if (c:=str(row['点评'])) and c.lower()!="none" and len(c)>1: st.markdown(f"<small style='color:gray'>💬 {c}</small>", unsafe_allow_html=True)
                m1, m2 = st.columns([2, 1.2])
                with m1: st.caption(f"{str(row['时间'])[5:16]} | {row['公众号']}")
                with m2: 
                    if str(row['原文']).startswith("http"): st.link_button("👉 阅读全文", str(row['原文']), type="primary", use_container_width=True)
                    else: st.button("🚫 无链接", disabled=True, use_container_width=True, key=f"btn_{row.name}")

def main():
    st.set_page_config(PAGE_TITLE, PAGE_ICON, layout="wide")
    init_state()
    wx, gem, scope, run, dbg, frc = render_sidebar()
    c_t1, c_t2 = st.columns([3, 1])
    c_t1.title(f"{PAGE_ICON} {PAGE_TITLE}")
    if not st.session_state.history_df.empty and '日期' in st.session_state.history_df.columns: 
        c_t2.metric("已读", len(st.session_state.history_df))
    
    if run:
        if not gem: st.error("❌ 缺Key")
        else:
            if dbg: st.warning("🐞 Debug On...")
            src, ana = WxSource(wx, dbg), AIAnalyst(gem, dbg)
            targets = st.session_state.config_df
            if '启用' in targets.columns: targets = targets[targets['启用'] == True]
            
            if targets.empty: st.warning("未选账号")
            else:
                bar, new_data = st.progress(0), []
                today = datetime.now().strftime('%Y-%m-%d')
                
                for i, r in enumerate(targets.itertuples()):
                    # 跳过逻辑
                    if not frc and scope == 0 and '日期' in st.session_state.history_df.columns:
                        if not st.session_state.history_df[(st.session_state.history_df['公众号'] == r.公众号) & (st.session_state.history_df['日期'].astype(str) == today)].empty:
                            st.toast(f"⏭️ {r.公众号}"); bar.progress((i+1)/len(targets)); continue
                    
                    st.toast(f"📖 {r.公众号}")
                    for a in src.get_scoped_articles(r.ID, scope):
                        # 铁三角去重
                        is_dup = False
                        if '标题' in st.session_state.history_df.columns:
                            if ((st.session_state.history_df['标题']==a['title']) & (st.session_state.history_df['公众号']==r.公众号) & (st.session_state.history_df['日期']==a['date'])).any(): is_dup = True
                        for nd in new_data:
                             if nd['标题']==a['title'] and nd['公众号']==r.公众号 and nd['日期']==a['date']: is_dup = True; break
                        
                        if not is_dup:
                            if txt := src.fetch_content(a['url']):
                                if res := ana.analyze(txt, a['title']):
                                    new_data.append({
                                        "日期": a['date'], "时间": a['full_time'][11:16], "公众号": r.公众号, "标题": a['title'], 
                                        "价值": int(res.get('score', 0)), "摘要": str(res.get('summary', '')), 
                                        "点评": str(res.get('suggestion', '')).replace("None", ""), "原文": a['url']
                                    })
                    bar.progress((i+1)/len(targets))
                if new_data:
                    st.toast("☁️ 同步中..."); df = st.session_state.data_manager.save_history(pd.DataFrame(new_data))
                    st.session_state.history_df = df; st.success(f"更新 {len(new_data)} 篇"); time.sleep(1); st.rerun()
                else: st.toast("✅ 无更新")
    render_results()

if __name__ == "__main__":
    main()
