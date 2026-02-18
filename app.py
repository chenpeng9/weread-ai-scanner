import streamlit as st
import requests
import json
import time
import re
import pandas as pd
import xml.etree.ElementTree as ET
import io
import os
from datetime import datetime, timedelta

# ==========================================
# 1. 全局配置与核心指令
# ==========================================
PAGE_TITLE = "WeRead AI"
PAGE_ICON = "🎯"
DEFAULT_XML_PATH = "WeChat Official Accounts List.xml"

# ⚠️ 核心 Skill：毒舌做空机构分析师
SYSTEM_INSTRUCTION = """
【角色】你是一位像“浑水调研”一样毒辣、冷血的顶级做空机构分析师。你对市场噪音极度不耐烦，对“割韭菜”的行为深恶痛绝。

【评分标准 (0-10分)】
* 0-2分 (垃圾/收割)：任何带货、卖课、团购、广告软文、单纯的情绪宣泄、无脑转发。
    * 关键词敏感度：出现“扫码”、“下单”、“课程”、“私董会”、“点击阅读原文”等词汇，直接打入冷宫。
* 3-5分 (平庸)：只有新闻罗列没有观点，或者观点是大路货（人云亦云）。
* 6-7分 (合格)：有基本的数据支撑和逻辑推演，值得一看。
* 8-10分 (Alpha)：极其稀缺的行业内幕、深度的宏观推演、甚至能指导交易的套利机会。

【输出要求】
1. 摘要(summary)：极度精简，50字内。如果是垃圾，直接写“带货软文，无视”或“情绪垃圾”。
2. 点评(suggestion)：15字内。使用毒舌风格，例如“想割韭菜，没门”、“毫无新意”、“建议拉黑”。
3. 必须输出纯 JSON 格式。

【JSON 结构】
{
    "summary": "核心摘要",
    "score": 2,
    "suggestion": "软广，别看",
    "sentiment": "看空"
}
"""

# ==========================================
# 2. 服务层 (API 交互)
# ==========================================
class WxSource:
    def __init__(self, api_key):
        self.api_key = api_key
        self.list_api = "http://data.wxrank.com/weixin/getps"
        self.content_api = "http://data.wxrank.com/weixin/artinfo"

    def get_scoped_articles(self, wxid, days_back=0):
        params = {"key": self.api_key, "wxid": wxid}
        target_dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days_back + 1)]
        try:
            resp = requests.get(self.list_api, params=params, timeout=10)
            data = resp.json()
            if str(data.get("code")) == "0":
                raw_list = data.get("data", {}).get("list", []) or data.get("data", [])
                matched = []
                for item in raw_list:
                    pub_time = item.get("pub_time") or ""
                    if any(pub_time.startswith(d) for d in target_dates):
                        matched.append({
                            "title": item.get("title") or item.get("msg_title"),
                            "url": item.get("url") or item.get("art_url"),
                            "date": pub_time[:10], "full_time": pub_time
                        })
                return matched
            return []
        except: return []

    def fetch_content(self, url):
        try:
            resp = requests.post(self.content_api, json={"key": self.api_key, "url": url}, timeout=20)
            if str(resp.json().get("code")) == "0":
                return resp.json().get("data", {}).get("text", "")[:8000]
            return ""
        except: return ""

class AIAnalyst:
    def __init__(self, api_key):
        # 默认使用 1.5-flash，如确认有 3.0 权限可改为 gemini-3-flash
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    def analyze(self, text, title):
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [{"text": f"分析文章《{title}》:\n{text}"}]}]
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            if resp.status_code != 200: return None
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else None
        except: return None

# ==========================================
# 3. 工具函数
# ==========================================
def parse_xml_config(source):
    try:
        ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}
        tree = ET.parse(source)
        root = tree.getroot()
        configs = []
        for i, row in enumerate(root.findall(".//ss:Row", ns)):
            if i == 0: continue
            cells = row.findall("ss:Cell", ns)
            if len(cells) >= 3:
                name_el = cells[1].find("ss:Data", ns)
                id_el = cells[2].find("ss:Data", ns)
                if name_el is not None and id_el is not None:
                    configs.append({"ID": id_el.text.strip(), "公众号": name_el.text.strip(), "启用": True})
        return pd.DataFrame(configs) if configs else None
    except Exception as e:
        return None

def init_session_state():
    if 'history_df' not in st.session_state:
        st.session_state.history_df = pd.DataFrame(columns=["日期", "时间", "公众号", "标题", "价值", "摘要", "点评", "原文"])

    if 'config_list' not in st.session_state:
        if os.path.exists(DEFAULT_XML_PATH):
            df = parse_xml_config(DEFAULT_XML_PATH)
            st.session_state.config_list = df if df is not None else pd.DataFrame(columns=["ID", "公众号", "启用"])
        else:
            st.session_state.config_list = pd.DataFrame([{"ID": "bullpiano", "公众号": "牛弹琴 (演示)", "启用": True}])

# ==========================================
# 4. 界面渲染 (逻辑修复核心)
# ==========================================
def render_sidebar():
    with st.sidebar:
        st.title(f"{PAGE_ICON} WeRead Alpha")
        
        # --- A. 密钥配置 (修复版：逻辑分离) ---
        # 1. WxRank Key
        if "WX_KEY" in st.secrets:
            wx_key = st.secrets["WX_KEY"]
            st.success("✅ WxRank Key 已云端加载")
        else:
            wx_key = st.text_input("WxRank API Key", value="5e1bde783213147e8907")

        # 2. Gemini Key
        if "GEMINI_KEY" in st.secrets:
            gemini_key = st.secrets["GEMINI_KEY"]
            st.success("✅ Gemini Key 已云端加载")
        else:
            gemini_key = st.text_input("Gemini API Key", type="password")
            if not gemini_key:
                st.info("👆 请输入 Gemini Key 以启用 AI 分析")

        st.divider()
        
        # --- B. 阅读范围 ---
        time_scope = st.selectbox("📅 阅读范围", options=[0, 1], format_func=lambda x: "仅今日 (24h)" if x == 0 else "今日 + 昨日 (48h)")
        
        # --- C. 手动导入 ---
        uploaded_file = st.file_uploader("📂 导入 Excel XML", type="xml")
        if uploaded_file:
            file_id = f"{uploaded_file.name}_{uploaded_file.size}"
            if st.session_state.get("last_xml_id") != file_id:
                df = parse_xml_config(uploaded_file)
                if df is not None:
                    st.session_state.config_list = df
                    st.session_state.last_xml_id = file_id
                    st.success(f"已加载 {len(df)} 个账号")
                    time.sleep(1)
                    st.rerun()

        st.divider()
        st.subheader("📁 账号管理")

        # --- D. 批量操作 ---
        col_b1, col_b2 = st.columns(2)
        if col_b1.button("✅ 全选", use_container_width=True):
            st.session_state.config_list["启用"] = True
            st.rerun()
        if col_b2.button("⬜ 全不选", use_container_width=True):
            st.session_state.config_list["启用"] = False
            st.rerun()

        # --- E. 列表编辑器 ---
        with st.form("account_manager_form"):
            display_df = st.session_state.config_list.copy()
            if '启用' in display_df.columns:
                display_df = display_df[['启用', '公众号', 'ID']]
            display_df.insert(1, '序号', range(1, len(display_df) + 1))
            
            edited_df = st.data_editor(
                display_df,
                column_config={
                    "启用": st.column_config.CheckboxColumn(label="✅", width="small"),
                    "序号": st.column_config.NumberColumn(width="small", disabled=True),
                    "公众号": st.column_config.TextColumn(width="medium", disabled=True),
                    "ID": None
                },
                hide_index=True, use_container_width=True, height=400
            )
            
            if st.form_submit_button("💾 保存状态", type="primary", use_container_width=True):
                st.session_state.config_list = edited_df.drop(columns=['序号'])[['ID', '公众号', '启用']]
                st.toast("✅ 账号状态已锁定")
                time.sleep(0.5)
                st.rerun()

        st.divider()
        
        # --- F. 主操作区 ---
        c1, c2 = st.columns(2)
        trigger = c1.button("🚀 开始阅读", type="primary", use_container_width=True)
        if c2.button("🗑️ 清空历史", use_container_width=True):
            st.session_state.history_df = st.session_state.history_df.iloc[0:0]
            st.rerun()
            
        return wx_key, gemini_key, time_scope, trigger

def render_results():
    if not st.session_state.history_df.empty:
        c1, c2 = st.columns([1, 4])
        c1.metric("今日捕获", len(st.session_state.history_df))
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            st.session_state.history_df.to_excel(writer, index=False, sheet_name='AuditReport')
            ws = writer.sheets['AuditReport']
            ws.set_column('D:D', 40)
            ws.set_column('F:F', 60)
            
        c2.download_button(
            label="📥 导出研报 (Excel)",
            data=buffer.getvalue(),
            file_name=f"WeRead_Audit_{datetime.now().strftime('%m%d')}.xlsx",
            mime="application/vnd.ms-excel"
        )

        def highlight_score(val):
            if isinstance(val, (int, float)):
                if val >= 8: return 'background-color: #d4edda; color: #155724; font-weight: bold'
                elif val >= 6: return 'background-color: #cce5ff; color: #004085'
                elif val >= 3: return 'background-color: #fff3cd; color: #856404'
                else: return 'background-color: #f8d7da; color: #721c24'
            return ''

        df_sorted = st.session_state.history_df.sort_values(by=["日期", "时间"], ascending=False)
        styled_df = df_sorted.style.map(highlight_score, subset=['价值'])

        st.dataframe(
            styled_df,
            column_config={
                "原文": st.column_config.LinkColumn("链接", display_text="🔗 直达"),
                "价值": st.column_config.NumberColumn("评分", format="%d 分"),
                "摘要": st.column_config.TextColumn("核心摘要", width="large"),
                "点评": st.column_config.TextColumn("毒舌点评", width="medium"),
            },
            hide_index=True, use_container_width=True, height=600
        )
    else:
        st.info("👋 暂无情报。请在左侧勾选账号并点击「开始阅读」。")

# ==========================================
# 5. 主程序逻辑
# ==========================================
def main():
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
    init_session_state()
    
    wx_key, gemini_key, time_scope, trigger = render_sidebar()
    st.title(f"{PAGE_ICON} {PAGE_TITLE} | 微读精选")

    if trigger:
        if not gemini_key:
            st.error("❌ 缺少 Gemini API Key，请配置 Secrets 或手动输入。")
        else:
            source = WxSource(wx_key)
            analyst = AIAnalyst(gemini_key)
            active_list = st.session_state.config_list[st.session_state.config_list["启用"] == True]
            
            st.toast(f"🎯 任务启动：锁定 {len(active_list)} 个目标")
            
            if active_list.empty:
                st.warning("⚠️ 列表为空！请先勾选账号并点击【💾 保存状态】。")
            else:
                progress_bar = st.progress(0)
                new_records = []
                for idx, row in enumerate(active_list.itertuples()):
                    st.toast(f"🕵️‍♂️ 阅读: {row.公众号}...")
                    articles = source.get_scoped_articles(row.ID, days_back=time_scope)
                    for art in articles:
                        if not (st.session_state.history_df['原文'] == art['url']).any():
                            content = source.fetch_content(art['url'])
                            if content:
                                res = analyst.analyze(content, art['title'])
                                if res:
                                    new_records.append({
                                        "日期": art['date'],
                                        "时间": art['full_time'][11:16],
                                        "公众号": row.公众号,
                                        "标题": art['title'],
                                        "价值": res.get('score', 0),
                                        "摘要": res.get('summary', ''),
                                        "点评": res.get('suggestion', ''),
                                        "原文": art['url']
                                    })
                    progress_bar.progress((idx + 1) / len(active_list))
                
                if new_records:
                    st.session_state.history_df = pd.concat([pd.DataFrame(new_records), st.session_state.history_df], ignore_index=True)
                    st.success(f"✅ 阅读完成，新增 {len(new_records)} 条情报")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.toast("✅ 扫描完成，今日暂无新内容")

    render_results()

if __name__ == "__main__":
    main()
