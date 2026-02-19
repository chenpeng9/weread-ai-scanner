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
DEFAULT_XML_PATH = "WeChat Official Accounts List.xml"
EXPECTED_COLS = ["日期", "时间", "公众号", "标题", "价值", "摘要", "点评", "原文"]

SYSTEM_INSTRUCTION = """
【角色】你是一位像“浑水调研”一样毒辣、冷血的顶级做空机构分析师。你对市场噪音极度不耐烦，对“割韭菜”的行为深恶痛绝。
【评分标准 (0-10分)】
* 0-2分 (垃圾/收割)：任何带货、卖课、团购、广告软文、单纯的情绪宣泄。
* 3-5分 (平庸)：只有新闻罗列没有观点。
* 6-7分 (合格)：有基本的数据支撑和逻辑推演。
* 8-10分 (Alpha)：极其稀缺的行业内幕、深度的宏观推演。
【输出要求】
1. 摘要：50字内。
2. 点评：15字内毒舌点评。
3. 必须输出纯 JSON，key 为 "summary", "score", "suggestion"。
"""

# ==========================================
# 2. 数据层 (防爆 + 暴力清空)
# ==========================================
class DataManager:
    def __init__(self):
        try:
            self.conn = st.connection("gsheets", type=GSheetsConnection)
            self.enabled = True
        except: self.enabled = False
            
    def load_data(self):
        if not self.enabled: return pd.DataFrame(columns=EXPECTED_COLS)
        try:
            df = self.conn.read(ttl=0)
            
            # 🛡️ 核心修复：防止 KeyError
            # 如果表是空的，或者关键列丢失，说明表坏了
            if df.empty or '日期' not in df.columns:
                return pd.DataFrame(columns=EXPECTED_COLS)
            
            # 补齐可能缺失的列
            for col in EXPECTED_COLS:
                if col not in df.columns: df[col] = ""
            
            # 只要标准列
            df = df[EXPECTED_COLS]
            
            # 🧹 强力清洗
            df['价值'] = pd.to_numeric(df['价值'], errors='coerce').fillna(0).astype(int)
            df['点评'] = df['点评'].fillna("").astype(str).replace("None", "").replace("nan", "")
            df['原文'] = df['原文'].fillna("").astype(str)
            df['日期'] = df['日期'].astype(str)
            
            return df
        except Exception as e:
            return pd.DataFrame(columns=EXPECTED_COLS)

    def save_data(self, new_df):
        if not self.enabled: return new_df
        try:
            new_df = new_df[EXPECTED_COLS]
            old = self.load_data()
            combined = pd.concat([new_df, old], ignore_index=True).drop_duplicates(subset=['原文'], keep='first')
            combined = combined.sort_values(by=["日期", "时间"], ascending=False)
            self.conn.update(data=combined)
            return combined
        except Exception as e:
            st.error(f"保存失败: {e}")
            return new_df

    def reset_data(self):
        """🚀 核按钮：暴力覆盖模式（清空云端）"""
        if not self.enabled: return pd.DataFrame(columns=EXPECTED_COLS)
        try:
            empty_df = pd.DataFrame(columns=EXPECTED_COLS)
            self.conn.update(data=empty_df)
            return empty_df
        except Exception as e:
            st.error(f"清空失败: {e}")
            return pd.DataFrame(columns=EXPECTED_COLS)

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
        except Exception as e:
            if self.debug: st.error(f"❌ WxSource: {str(e)}")
            return []

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
            timeout = 30
            if self.debug: st.caption(f"🤖 调用: {model}...")
            r = requests.post(self._get_url(model), json=payload, timeout=timeout)
            
            if r.status_code == 200:
                raw = r.json()['candidates'][0]['content']['parts'][0]['text']
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                return json.loads(match.group(0)) if match else None
            else:
                if self.debug: st.warning(f"⚠️ {model} 异常: {r.status_code}")
                return None
        except Exception as e:
            if self.debug: st.warning(f"⚠️ {model} 失败: {str(e)}")
            return None

    def analyze(self, text, title):
        time.sleep(0.3)
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]}, 
            "contents": [{"parts": [{"text": f"分析《{title}》:\n{text}"}]}]
        }
        shuffled_models = self.models.copy()
        random.shuffle(shuffled_models)
        
        for model in shuffled_models:
            result = self._try_request(model, payload)
            if result:
                if self.debug: st.toast(f"✅ {model} 成功")
                return result
        
        if self.debug: st.error("❌ 2.0 和 2.5 均无响应")
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

# 🛠️ 核心修复：init_state 增加强校验
def init_state():
    if 'data_manager' not in st.session_state: st.session_state.data_manager = DataManager()
    
    # 强制重新加载
    if 'history_df' not in st.session_state: 
        st.session_state.history_df = st.session_state.data_manager.load_data()
    
    # 🚨 熔断机制：如果内存里的表是坏的（没有日期列），立刻重置为空表
    # 这能防止 render_results 因为找不到列而崩溃
    if '日期' not in st.session_state.history_df.columns:
        st.session_state.history_df = pd.DataFrame(columns=EXPECTED_COLS)
    
    if 'config_list' not in st.session_state:
        df = parse_xml(DEFAULT_XML_PATH) if os.path.exists(DEFAULT_XML_PATH) else None
        st.session_state.config_list = df if df is not None else pd.DataFrame([{"ID": "bullpiano", "公众号": "牛弹琴 (演示)", "启用": True}])

# ==========================================
# 5. 界面渲染
# ==========================================
def render_sidebar():
    with st.sidebar:
        st.title(f"{PAGE_ICON} WeRead AI")
        if "WX_KEY" in st.secrets:
            wx_key = st.secrets["WX_KEY"]; st.success("✅ WxRank Key 已云端加载")
        else: wx_key = st.text_input("WxRank API Key", value="5e1bde783213147e8907")

        if "GEMINI_KEY" in st.secrets:
            gemini_key = st.secrets["GEMINI_KEY"]; st.success("✅ Gemini Key 已云端加载")
        else: gemini_key = st.text_input("Gemini API Key", type="password")
        
        st.divider()
        c1, c2 = st.columns(2)
        debug = c1.toggle("🐞 Debug", False)
        force = c2.toggle("⚡ 强刷", False)
        
        if debug and gemini_key:
            if st.button("🧪 测试连通性", width="stretch"):
                ana = AIAnalyst(gemini_key, debug=True)
                code, msg = ana.test_connection()
                if code == 200: st.toast("✅ Gemini 连接通畅！", icon="🚀")
                else: st.error(f"连接失败 ({code}): {msg}")

        time_scope = st.selectbox("📅 范围", [0, 1], format_func=lambda x: "仅今日" if x == 0 else "近48小时")
        
        if u := st.file_uploader("📂 导入XML", "xml"):
            if st.session_state.get("last_xml") != u.name:
                if (df := parse_xml(u)) is not None:
                    st.session_state.config_list = df
                    st.session_state.last_xml = u.name
                    st.rerun()

        st.divider()
        st.caption("账号管理")
        c1, c2 = st.columns(2)
        if c1.button("✅ 全选", width="stretch"): st.session_state.config_list["启用"] = True; st.rerun()
        if c2.button("⬜ 全不选", width="stretch"): st.session_state.config_list["启用"] = False; st.rerun()

        with st.form("acc_form"):
            df = st.session_state.config_list.copy()
            if '启用' in df.columns: df = df[['启用', '公众号', 'ID']]
            df.insert(1, 'No.', range(1, len(df)+1))
            edited = st.data_editor(
                df, 
                column_config={"启用": st.column_config.CheckboxColumn("✅", width="small"), "No.": st.column_config.NumberColumn(width="small"), "公众号": st.column_config.TextColumn(width="medium"), "ID": None},
                hide_index=True, width="stretch", height=300
            )
            if st.form_submit_button("💾 保存", type="primary", width="stretch"):
                st.session_state.config_list = edited.drop(columns=['No.'])[['ID', '公众号', '启用']]
                st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        trigger = c1.button("🚀 开始", type="primary", width="stretch") 
        if c2.button("🗑️ 清空历史", width="stretch"):
            # 调用新逻辑
            st.session_state.history_df = st.session_state.data_manager.reset_data()
            st.rerun()
            
        return wx_key, gemini_key, time_scope, trigger, debug, force

def render_results():
    # 🛑 防御性检查：如果是空表，直接跳过渲染
    if st.session_state.history_df.empty:
        st.info("👋 暂无记录，请点击侧边栏「🚀 开始」")
        return

    # 🛑 核心防御：如果表里没有日期列（坏数据），显示修复提示，而不是报错崩溃
    if '日期' not in st.session_state.history_df.columns:
        st.error("⚠️ 数据结构异常：检测到坏数据。请点击侧边栏的【🗑️ 清空历史】按钮来重置数据库。")
        return

    col1, col2 = st.columns([1.5, 1])
    with col1:
        raw_dates = st.session_state.history_df['日期'].astype(str).dropna().unique().tolist()
        valid_dates = [d for d in raw_dates if d.lower() != 'nan' and len(d) > 0]
        all_dates = ["全部"] + sorted(valid_dates, reverse=True)
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
            hide_index=True, width="stretch", height=600
        )
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("📥 导出Excel", b.getvalue(), f"WeRead_{datetime.now():%m%d}.xlsx", width="stretch")

    else:
        sort_mode = st.radio("排序", ["⏱️ 时间倒序", "🔥 评分最高"], horizontal=True, label_visibility="collapsed")
        if sort_mode == "🔥 评分最高":
            df_sorted = df.sort_values(by=["价值", "日期", "时间"], ascending=[False, False, False])
        else:
            df_sorted = df.sort_values(by=["日期", "时间"], ascending=[False, False])

        if df_sorted.empty: st.info("📭 无记录")
        
        for _, row in df_sorted.iterrows():
            try: score = int(row['价值'])
            except: score = 0
            if score >= 8: color, icon = "green", "🟢"
            elif score >= 6: color, icon = "blue", "🔵"
            elif score >= 3: color, icon = "orange", "🟡"
            else: color, icon = "red", "🔴"

            with st.container(border=True):
                c_head, c_score = st.columns([3.5, 1])
                c_head.markdown(f"**{row['标题']}**")
                c_score.markdown(f":{color}[**{score}分**]")
                st.caption(f"💡 {row['摘要']}")
                comment = str(row['点评'])
                if comment and comment.lower() != "none" and len(comment) > 1:
                    st.markdown(f"<small style='color:gray'>💬 {comment}</small>", unsafe_allow_html=True)
                c_meta, c_btn = st.columns([2, 1.2])
                with c_meta: st.caption(f"{str(row['时间'])[5:16]} | {row['公众号']}")
                with c_btn:
                    url = str(row['原文']).strip()
                    if url.startswith("http"): st.link_button("👉 阅读全文", url, type="primary", width="stretch")
                    else: st.button("🚫 无链接", disabled=True, width="stretch", key=f"btn_{row.name}")

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
            if dbg: st.warning("🐞 调试模式已开启：正在输出详细 API 日志...")
            src, ana = WxSource(wx, dbg), AIAnalyst(gem, dbg)
            targets = st.session_state.config_list[st.session_state.config_list["启用"]==True]
            if targets.empty: st.warning("未选账号")
            else:
                bar, new_data = st.progress(0), []
                today = datetime.now().strftime('%Y-%m-%d')
                for i, r in enumerate(targets.itertuples()):
                    if not frc and '日期' in st.session_state.history_df.columns and not st.session_state.history_df[(st.session_state.history_df['公众号']==r.公众号) & (st.session_state.history_df['日期'].astype(str)==today)].empty:
                        st.toast(f"⏭️ 跳过 {r.公众号}"); bar.progress((i+1)/len(targets)); continue
                    st.toast(f"📖 {r.公众号}")
                    for a in src.get_scoped_articles(r.ID, scope):
                        # 检查重复也加防御
                        if '原文' in st.session_state.history_df.columns and not (st.session_state.history_df['原文']==a['url']).any():
                            if txt := src.fetch_content(a['url']):
                                if res := ana.analyze(txt, a['title']):
                                    score = int(res.get('score', 0))
                                    comment = str(res.get('suggestion', '')).replace("None", "")
                                    new_data.append({
                                        **a, 
                                        "时间": a['full_time'][11:16], 
                                        "公众号": r.公众号, 
                                        "价值": score, 
                                        "摘要": str(res.get('summary', '')), 
                                        "点评": comment, 
                                        "原文": a['url']
                                    })
                    bar.progress((i+1)/len(targets))
                if new_data:
                    st.toast("☁️ 同步中..."); df = st.session_state.data_manager.save_data(pd.DataFrame(new_data))
                    st.session_state.history_df = df; st.success(f"更新 {len(new_data)} 篇"); time.sleep(1); st.rerun()
                else: st.toast("✅ 无更新")
    render_results()

if __name__ == "__main__":
    main()
