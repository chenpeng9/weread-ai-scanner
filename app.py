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
PAGE_TITLE = "WeRead AI (微读精选)"
PAGE_ICON = "📖"
DEFAULT_XML_PATH = "WeChat Official Accounts List.xml"

# ⚠️ 核心 Skill：毒舌做空机构分析师 (完全保留)
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
# 2. 服务层 (API 交互 - 带 Debug)
# ==========================================
class WxSource:
    def __init__(self, api_key, debug_mode=False):
        self.api_key = api_key
        self.debug_mode = debug_mode
        self.list_api = "http://data.wxrank.com/weixin/getps"
        self.content_api = "http://data.wxrank.com/weixin/artinfo"

    def get_scoped_articles(self, wxid, days_back=0):
        params = {"key": self.api_key, "wxid": wxid}
        target_dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days_back + 1)]
        
        try:
            resp = requests.get(self.list_api, params=params, timeout=10)
            data = resp.json()
            
            # 🐞 Debug 输出
            if self.debug_mode:
                st.markdown(f"**[Debug] WxRank List API ({wxid})**")
                st.json(data)

            if str(data.get("code")) == "0":
                raw_list = data.get("data", {}).get("list", []) or data.get("data", [])
                matched = []
                for item in raw_list:
                    pub_time = item.get("pub_time") or ""
                    # 🐞 Debug: 打印每篇文章时间比对
                    if self.debug_mode:
                        st.text(f"  - 文章: {item.get('title')} | 时间: {pub_time} | 目标: {target_dates}")
                        
                    if any(pub_time.startswith(d) for d in target_dates):
                        matched.append({
                            "title": item.get("title") or item.get("msg_title"),
                            "url": item.get("url") or item.get("art_url"),
                            "date": pub_time[:10], "full_time": pub_time
                        })
                return matched
            return []
        except Exception as e:
            if self.debug_mode: st.error(f"❌ WxRank 请求失败: {e}")
            return []

    def fetch_content(self, url):
        try:
            resp = requests.post(self.content_api, json={"key": self.api_key, "url": url}, timeout=20)
            data = resp.json()
            
            # 🐞 Debug 输出 (只打印状态码，避免刷屏)
            if self.debug_mode:
                st.text(f"  [Debug] Fetch Content Code: {data.get('code')}")
                
            if str(data.get("code")) == "0":
                return data.get("data", {}).get("text", "")[:8000]
            return ""
        except: return ""

class AIAnalyst:
    def __init__(self, api_key, debug_mode=False):
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        self.debug_mode = debug_mode

    def analyze(self, text, title):
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [{"text": f"分析文章《{title}》:\n{text}"}]}]
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            
            if self.debug_mode:
                 st.text(f"  [Debug] Gemini Status: {resp.status_code}")
                 
            if resp.status_code != 200: 
                if self.debug_mode: st.error(f"Gemini Error: {resp.text}")
                return None
            
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else None
        except Exception as e:
            if self.debug_mode: st.error(f"Gemini Exception: {e}")
            return None

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
# 4. 界面渲染
# ==========================================
def render_sidebar():
    with st.sidebar:
        st.title(f"{PAGE_ICON} WeRead AI")
        
        # --- A. 密钥配置 ---
        if "WX_KEY" in st.secrets:
            wx_key = st.secrets["WX_KEY"]
            st.success("✅ WxRank Key 已云端加载")
        else:
            wx_key = st.text_input("WxRank API Key", type="password")

        if "GEMINI_KEY" in st.secrets:
            gemini_key = st.secrets["GEMINI_KEY"]
            st.success("✅ Gemini Key 已云端加载")
        else:
            gemini_key = st.text_input("Gemini API Key", type="password")
            if not gemini_key:
                st.info("👆 请输入 Gemini Key")

        st.divider()
        
        # --- ✨ 新增：Debug 开关 ---
        debug_mode = st.toggle("🐞 开启 Debug 模式", value=False)
        
        st.divider()
        
        # --- B. 范围设置 ---
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
        if col_b1.button("✅ 全选", width="stretch"):
            st.session_state.config_list["启用"] = True
            st.rerun()
        if col_b2.button("⬜ 全不选", width="stretch"):
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
                hide_index=True, width="stretch", height=400
            )
            
            if st.form_submit_button("💾 保存状态", type="primary", width="stretch"):
                st.session_state.config_list = edited_df.drop(columns=['序号'])[['ID', '公众号', '启用']]
                st.toast("✅ 账号状态已锁定")
                time.sleep(0.5)
                st.rerun()

        st.divider()
        
        # --- F. 主操作区 ---
        c1, c2 = st.columns(2)
        trigger = c1.button("🚀 开始阅读", type="primary", width="stretch")
        if c2.button("🗑️ 清空历史", width="stretch"):
            st.session_state.history_df = st.session_state.history_df.iloc[0:0]
            st.rerun()
            
        return wx_key, gemini_key, time_scope, trigger, debug_mode

def render_results():
    if not st.session_state.history_df.empty:
        c1, c2 = st.columns([1, 4])
        c1.metric("今日已读", len(st.session_state.history_df))
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            st.session_state.history_df.to_excel(writer, index=False, sheet_name='WeRead_Report')
            ws = writer.sheets['WeRead_Report']
            ws.set_column('D:D', 40)
            ws.set_column('F:F', 60)
            
        c2.download_button(
            label="📥 导出阅读笔记 (Excel)",
            data=buffer.getvalue(),
            file_name=f"WeRead_Notes_{datetime.now().strftime('%m%d')}.xlsx",
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
            hide_index=True, width="stretch", height=600
        )
    else:
        st.info("👋 暂无阅读记录。请在左侧勾选账号并点击「开始阅读」。")

# ==========================================
# 5. 主程序逻辑
# ==========================================
def main():
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
    init_session_state()
    
    # ✨ 接收 debug_mode 参数
    wx_key, gemini_key, time_scope, trigger, debug_mode = render_sidebar()
    
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")

    if trigger:
        if not gemini_key:
            st.error("❌ 缺少 Gemini API Key。")
        else:
            # ✨ 将 debug_mode 传入服务类
            source = WxSource(wx_key, debug_mode=debug_mode)
            analyst = AIAnalyst(gemini_key, debug_mode=debug_mode)
            
            active_list = st.session_state.config_list[st.session_state.config_list["启用"] == True]
            
            st.toast(f"🎯 任务启动：准备阅读 {len(active_list)} 个公众号")
            
            # 🐞 Debug 区域
            if debug_mode:
                st.warning("⚠️ 调试模式已开启，将显示详细 API 日志...")
            
            if active_list.empty:
                st.warning("⚠️ 列表为空！请先勾选账号并点击【💾 保存状态】。")
            else:
                progress_bar = st.progress(0)
                new_records = []
                for idx, row in enumerate(active_list.itertuples()):
                    st.toast(f"📖 正在阅读: {row.公众号}...")
                    
                    # 🐞 Debug: 打印当前正在处理的账号
                    if debug_mode:
                        st.markdown(f"---")
                        st.markdown(f"#### 🔎 正在检查: {row.公众号} (ID: {row.ID})")
                        
                    articles = source.get_scoped_articles(row.ID, days_back=time_scope)
                    
                    if debug_mode and not articles:
                        st.caption("⚠️ 该账号在指定范围内无文章，或 API 返回为空。")

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
                    st.success(f"✅ 阅读完成，更新 {len(new_records)} 篇文章")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.toast("✅ 阅读完成，今日暂无更新")

    render_results()

if __name__ == "__main__":
    main()
