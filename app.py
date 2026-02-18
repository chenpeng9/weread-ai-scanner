import streamlit as st
import requests
import json
import time
import re
import pandas as pd
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import io

# ==========================================
# 1. 系统指令 (毒舌审计师)
# ==========================================
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
            return resp.json().get("data", {}).get("text", "")[:6000] if str(resp.json().get("code")) == "0" else ""
        except: return ""

class AIAnalyst:
    def __init__(self, api_key):
        self.api_key = api_key
        # ✨ 这里把模型换成了 gemini-1.5-flash (目前最通用)。
        # 如果你确定你的 key 支持 gemini-3-flash，请直接修改下面的字符串为 gemini-3-flash
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash:generateContent?key={api_key}"

    def analyze(self, text, title):
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [{"text": f"分析文章《{title}》:\n{text}"}]}]
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            if resp.status_code != 200:
                return None
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else None
        except: return None

# ==========================================
# 2. 初始化
# ==========================================
st.set_page_config(page_title="WeRead AI", page_icon="💀", layout="wide")

if 'history_df' not in st.session_state:
    st.session_state.history_df = pd.DataFrame(columns=["日期", "时间", "公众号", "标题", "价值", "摘要", "点评", "原文"])

if 'config_list' not in st.session_state:
    st.session_state.config_list = pd.DataFrame([{"ID": "bullpiano", "公众号": "牛弹琴", "启用": True}])

# ==========================================
# 3. 侧边栏
# ==========================================
with st.sidebar:
    st.title("📖 WeRead AI")
    
    # ✨ 密钥管理升级：优先从 Secrets 读取，读不到才显示输入框
    if "WX_KEY" in st.secrets:
        wx_key = st.secrets["WX_KEY"]
        st.success("✅ WxRank Key 已从云端加载")
    else:
        wx_key = st.text_input("WxRank API Key", type="password")
        
    if "GEMINI_KEY" in st.secrets:
        gemini_key = st.secrets["GEMINI_KEY"]
        st.success("✅ Gemini Key 已从云端加载")
    else:
        gemini_key = st.text_input("Gemini API Key", type="password")
    
    st.divider()
    time_scope = st.selectbox("📅 获取范围", options=[0, 1], format_func=lambda x: "仅获取今日" if x == 0 else "获取今日+昨日")
    
    st.divider()
    xml_file = st.file_uploader("📂 导入 Excel XML", type="xml", key="xml_u")
    if xml_file:
        file_id = f"{xml_file.name}_{xml_file.size}"
        if st.session_state.get("last_xml") != file_id:
            try:
                ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}
                tree = ET.parse(xml_file)
                new_c = []
                for i, row in enumerate(tree.getroot().findall(".//ss:Row", ns)):
                    if i == 0: continue 
                    cells = row.findall("ss:Cell", ns)
                    if len(cells) >= 3:
                        name_el, id_el = cells[1].find("ss:Data", ns), cells[2].find("ss:Data", ns)
                        if name_el is not None and id_el is not None:
                            new_c.append({"ID": id_el.text.strip(), "公众号": name_el.text.strip(), "启用": True})
                if new_c:
                    st.session_state.config_list = pd.DataFrame(new_c)
                    st.session_state.last_xml = file_id
                    st.success(f"导入成功: {len(new_c)} 个账号")
                    st.rerun()
            except Exception as e:
                st.error(f"导入失败: {e}")

    st.subheader("📁 账号管理 (批量编辑)")

    c1, c2 = st.columns(2)
    if c1.button("✅ 全选", use_container_width=True):
        st.session_state.config_list["启用"] = True
        st.rerun()
    if c2.button("⬜ 全不选", use_container_width=True):
        st.session_state.config_list["启用"] = False
        st.rerun()

    with st.form("account_form"):
        display_df = st.session_state.config_list.copy()
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
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            height=400, 
            key="main_editor"
        )
        
        submit_changes = st.form_submit_button("💾 保存选中状态", type="primary", use_container_width=True)

    if submit_changes:
        save_df = edited_df.drop(columns=['序号'])
        st.session_state.config_list = save_df[['ID', '公众号', '启用']]
        st.toast("✅ 状态已保存！")
        time.sleep(0.5)
        st.rerun()

    st.divider()
    col1, col2 = st.columns(2)
    refresh_btn = col1.button("🚀 开始扫描", type="primary", use_container_width=True)
    if col2.button("🗑️ 清空历史", use_container_width=True):
        st.session_state.history_df = st.session_state.history_df.iloc[0:0]
        st.rerun()

    if st.button("🚫 重置所有", use_container_width=True):
        st.session_state.config_list = pd.DataFrame(columns=["ID", "公众号", "启用"])
        st.session_state.last_xml = None
        st.rerun()

# ==========================================
# 4. 刷新逻辑
# ==========================================
st.title("📖 WeRead AI (微读精选)")

if refresh_btn:
    if not gemini_key:
        st.error("请配置 Gemini API Key")
    else:
        source, analyst = WxSource(wx_key), AIAnalyst(gemini_key)
        
        active_list = st.session_state.config_list[st.session_state.config_list["启用"] == True]
        st.toast(f"任务启动：扫描 {len(active_list)} 个目标")
        
        if active_list.empty:
            st.warning("⚠️ 请先勾选账号，并点击【💾 保存选中状态】按钮！")
        else:
            prog = st.progress(0)
            new_recs = []
            
            for idx, row in enumerate(active_list.itertuples()):
                st.toast(f"审计: {row.公众号}")
                arts = source.get_scoped_articles(row.ID, days_back=time_scope)
                for art in arts:
                    if not (st.session_state.history_df['原文'] == art['url']).any():
                        content = source.fetch_content(art['url'])
                        if content:
                            res = analyst.analyze(content, art['title'])
                            if res:
                                new_recs.append({
                                    "日期": art['date'], "时间": art['full_time'][11:16],
                                    "公众号": row.公众号, "标题": art['title'],
                                    "价值": res.get('score', 0), "摘要": res.get('summary', ''),
                                    "点评": res.get('suggestion', ''), "原文": art['url']
                                })
                prog.progress((idx + 1) / len(active_list))
            
            if new_recs:
                st.session_state.history_df = pd.concat([pd.DataFrame(new_recs), st.session_state.history_df], ignore_index=True)
            st.rerun()

# ==========================================
# 5. 结果展示
# ==========================================
if not st.session_state.history_df.empty:
    m1, m2 = st.columns([1, 4])
    m1.metric("今日捕获", len(st.session_state.history_df))
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        st.session_state.history_df.to_excel(writer, index=False, sheet_name='DailyReport')
        ws = writer.sheets['DailyReport']
        ws.set_column('D:D', 40)
        ws.set_column('F:F', 60)
    m2.download_button("📥 导出报表", data=buffer.getvalue(), file_name=f"WeRead_Audit_{datetime.now().strftime('%m%d')}.xlsx")

    st.dataframe(
        st.session_state.history_df.sort_values(by=["日期", "时间"], ascending=False),
        column_config={
            "原文": st.column_config.LinkColumn("阅读", display_text="🔗 直达"),
            "价值": st.column_config.ProgressColumn("价值评分", min_value=0, max_value=10, format="%d"),
            "摘要": st.column_config.TextColumn("核心摘要 (50字)", width="large"),
        },
        hide_index=True, use_container_width=True
    )
else:
    st.info("👋 暂无数据。请在左侧【保存】勾选状态并刷新。")
