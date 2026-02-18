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
from streamlit_gsheets import GSheetsConnection

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
# 2. 数据持久化层 (Google Sheets)
# ==========================================
class DataManager:
    """负责与 Google Sheets 进行数据同步"""
    def __init__(self):
        try:
            self.conn = st.connection("gsheets", type=GSheetsConnection)
            self.enabled = True
        except Exception:
            self.enabled = False
            
    def load_data(self):
        """从云端加载数据，如果配置失败则返回空表"""
        expected_cols = ["日期", "时间", "公众号", "标题", "价值", "摘要", "点评", "原文"]
        if not self.enabled:
            return pd.DataFrame(columns=expected_cols)
        
        try:
            # ttl=0 强制不缓存
            df = self.conn.read(ttl=0)
            # 处理空表或列不匹配的情况
            if df.empty or not all(col in df.columns for col in expected_cols):
                return pd.DataFrame(columns=expected_cols)
            # 确保价值列是数字类型
            df['价值'] = pd.to_numeric(df['价值'], errors='coerce').fillna(0).astype(int)
            return df
        except Exception as e:
            st.warning(f"⚠️ 无法连接数据库，将使用临时会话模式。错误: {e}")
            return pd.DataFrame(columns=expected_cols)

    def save_data(self, new_df):
        """保存数据到云端 (增量更新)"""
        if not self.enabled:
            return new_df # 如果没开启云端，直接返回新数据用于本地显示
            
        try:
            # 1. 先拉取最新数据（防止覆盖他人操作）
            existing_df = self.load_data()
            
            # 2. 合并
            combined_df = pd.concat([new_df, existing_df], ignore_index=True)
            
            # 3. 去重 (以URL为键，保留最新的)
            combined_df = combined_df.drop_duplicates(subset=['原文'], keep='first')
            
            # 4. 排序
            combined_df = combined_df.sort_values(by=["日期", "时间"], ascending=False)
            
            # 5. 写回
            self.conn.update(data=combined_df)
            st.toast("☁️ 数据已同步至 Google Sheets")
            return combined_df
        except Exception as e:
            st.error(f"❌ 数据同步失败: {e}")
            return new_df

# ==========================================
# 3. 服务层 (API 交互)
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
            
            if self.debug_mode:
                st.write(f"🔍 [WxRank] ID: {wxid} | Code: {data.get('code')}")

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
        except Exception as e:
            if self.debug_mode: st.error(f"WxRank Error: {e}")
            return []

    def fetch_content(self, url):
        try:
            resp = requests.post(self.content_api, json={"key": self.api_key, "url": url}, timeout=20)
            if str(resp.json().get("code")) == "0":
                return resp.json().get("data", {}).get("text", "")[:8000]
            return ""
        except: return ""

class AIAnalyst:
    def __init__(self, api_key, debug_mode=False):
        # ✅ 修复：锁定 gemini-2.0-flash
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        self.debug_mode = debug_mode
        self.api_key = api_key

    def list_available_models(self):
        """调试工具：列出可用模型"""
        try:
            list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
            resp = requests.get(list_url, timeout=10)
            if resp.status_code == 200:
                models = resp.json().get('models', [])
                return [m['name'] for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
            return [f"Error: {resp.status_code}"]
        except Exception as e: return [str(e)]

    def analyze(self, text, title):
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [{"text": f"分析文章《{title}》:\n{text}"}]}]
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            
            if self.debug_mode:
                if resp.status_code != 200: st.error(f"Gemini Error ({resp.status_code}): {resp.text}")
            
            if resp.status_code != 200: return None
            
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else None
        except Exception as e:
            if self.debug_mode: st.error(f"Gemini Exception: {e}")
            return None

# ==========================================
# 4. 工具函数
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
    except: return None

def init_session_state():
    # 1. 初始化数据管理器
    if 'data_manager' not in st.session_state:
        st.session_state.data_manager = DataManager()
        
    # 2. 从云端加载历史数据
    if 'history_df' not in st.session_state:
        with st.spinner("☁️ 正在连接情报数据库..."):
            st.session_state.history_df = st.session_state.data_manager.load_data()

    # 3. 加载公众号列表
    if 'config_list' not in st.session_state:
        if os.path.exists(DEFAULT_XML_PATH):
            df = parse_xml_config(DEFAULT_XML_PATH)
            st.session_state.config_list = df if df is not None else pd.DataFrame(columns=["ID", "公众号", "启用"])
        else:
            st.session_state.config_list = pd.DataFrame([{"ID": "bullpiano", "公众号": "牛弹琴 (演示)", "启用": True}])

# ==========================================
# 5. 界面渲染
# ==========================================
def render_sidebar():
    with st.sidebar:
        st.title(f"{PAGE_ICON} WeRead AI")
        
        # --- A. 密钥配置 ---
        if "WX_KEY" in st.secrets:
            wx_key = st.secrets["WX_KEY"]
            st.success("✅ WxRank Key 已加载")
        else:
            wx_key = st.text_input("WxRank API Key", value="5e1bde783213147e8907")

        if "GEMINI_KEY" in st.secrets:
            gemini_key = st.secrets["GEMINI_KEY"]
            st.success("✅ Gemini Key 已加载")
        else:
            gemini_key = st.text_input("Gemini API Key", type="password")

        st.divider()
        debug_mode = st.toggle("🐞 Debug 模式", value=False)
        
        # --- B. 调试工具 ---
        if debug_mode and gemini_key:
            if st.button("🔍 检查可用模型"):
                analyst = AIAnalyst(gemini_key)
                models = analyst.list_available_models()
                st.info("Available Models:")
                st.code("\n".join(models))
        
        st.divider()
        time_scope = st.selectbox("📅 阅读范围", options=[0, 1], format_func=lambda x: "仅今日" if x == 0 else "今日+昨日")
        
        # --- C. 导入列表 ---
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
        c1, c2 = st.columns(2)
        if c1.button("✅ 全选", width="stretch"):
            st.session_state.config_list["启用"] = True
            st.rerun()
        if c2.button("⬜ 全不选", width="stretch"):
            st.session_state.config_list["启用"] = False
            st.rerun()

        with st.form("account_manager_form"):
            display_df = st.session_state.config_list.copy()
            if '启用' in display_df.columns: display_df = display_df[['启用', '公众号', 'ID']]
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
                st.toast("✅ 状态已锁定")
                time.sleep(0.5)
                st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        trigger = c1.button("🚀 开始阅读", type="primary", width="stretch")
        if c2.button("🗑️ 清空历史", width="stretch"):
            st.session_state.history_df = st.session_state.history_df.iloc[0:0]
            st.rerun()
            
        return wx_key, gemini_key, time_scope, trigger, debug_mode

def render_results():
    if not st.session_state.history_df.empty:
        c1, c2 = st.columns([1, 2])
        c1.metric("今日已读", len(st.session_state.history_df))
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            st.session_state.history_df.to_excel(writer, index=False, sheet_name='Report')
        c2.download_button("📥 导出 Excel", data=buffer.getvalue(), file_name=f"WeRead_{datetime.now().strftime('%m%d')}.xlsx", use_container_width=True)

        st.write("---")

        # ✨ 手机/桌面 视图切换
        tab_mobile, tab_desktop = st.tabs(["📱 手机模式", "💻 桌面模式"])

        # 手机视图 (Card View)
        with tab_mobile:
            df_sorted = st.session_state.history_df.sort_values(by=["日期", "时间"], ascending=False)
            for index, row in df_sorted.iterrows():
                try:
                    score = int(row['价值'])
                except: score = 0
                
                if score >= 8: border = "#d4edda"; badge = f"🟢 **{score} 分 (Alpha)**"
                elif score >= 6: border = "#cce5ff"; badge = f"🔵 **{score} 分 (合格)**"
                elif score >= 3: border = "#fff3cd"; badge = f"🟡 **{score} 分 (平庸)**"
                else: border = "#f8d7da"; badge = f"🔴 **{score} 分 (垃圾)**"

                with st.container(border=True):
                    st.markdown(f"### {row['标题']}")
                    st.markdown(badge)
                    st.info(f"💡 {row['摘要']}")
                    if row['点评'] and len(str(row['点评'])) > 1:
                        st.markdown(f"> 💬 {row['点评']}")
                    c_a, c_b = st.columns([2, 1])
                    c_a.caption(f"{row['时间']} | {row['公众号']}")
                    st.link_button("👉 阅读原文", row['原文'], use_container_width=True)

        # 桌面视图 (Table View)
        with tab_desktop:
            def highlight_score(val):
                if isinstance(val, (int, float)):
                    if val >= 8: return 'background-color: #d4edda; color: #155724; font-weight: bold'
                    elif val >= 6: return 'background-color: #cce5ff; color: #004085'
                    elif val >= 3: return 'background-color: #fff3cd; color: #856404'
                    else: return 'background-color: #f8d7da; color: #721c24'
                return ''

            st.dataframe(
                st.session_state.history_df.sort_values(by=["日期", "时间"], ascending=False).style.map(highlight_score, subset=['价值']),
                column_config={
                    "原文": st.column_config.LinkColumn("链接", display_text="🔗"),
                    "价值": st.column_config.NumberColumn("分", format="%d"),
                    "摘要": st.column_config.TextColumn("摘要", width="large"),
                    "点评": st.column_config.TextColumn("点评", width="medium"),
                },
                hide_index=True, use_container_width=True
            )
    else:
        st.info("👋 暂无阅读记录。请在左侧勾选账号并点击「开始阅读」。")

# ==========================================
# 6. 主程序
# ==========================================
def main():
    st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")
    init_session_state()
    
    wx_key, gemini_key, time_scope, trigger, debug_mode = render_sidebar()
    st.title(f"{PAGE_ICON} {PAGE_TITLE}")

    if trigger:
        if not gemini_key:
            st.error("❌ 缺少 Gemini Key")
        else:
            source = WxSource(wx_key, debug_mode)
            analyst = AIAnalyst(gemini_key, debug_mode)
            active_list = st.session_state.config_list[st.session_state.config_list["启用"] == True]
            
            st.toast(f"🎯 任务启动：{len(active_list)} 个目标")
            if debug_mode: st.warning("🐞 调试模式运行中...")

            if active_list.empty:
                st.warning("⚠️ 请先勾选账号")
            else:
                progress_bar = st.progress(0)
                new_records = []
                for idx, row in enumerate(active_list.itertuples()):
                    st.toast(f"📖 {row.公众号}")
                    
                    articles = source.get_scoped_articles(row.ID, days_back=time_scope)
                    
                    # Debug Log
                    if debug_mode:
                        st.caption(f"检查: {row.公众号} | 发现: {len(articles)} 篇")

                    for art in articles:
                        # 检查本地和云端是否已有记录
                        if not (st.session_state.history_df['原文'] == art['url']).any():
                            content = source.fetch_content(art['url'])
                            if content:
                                res = analyst.analyze(content, art['title'])
                                if res:
                                    new_records.append({
                                        "日期": art['date'], "时间": art['full_time'][11:16],
                                        "公众号": row.公众号, "标题": art['title'],
                                        "价值": res.get('score', 0), "摘要": res.get('summary', ''),
                                        "点评": res.get('suggestion', ''), "原文": art['url']
                                    })
                    progress_bar.progress((idx + 1) / len(active_list))
                
                if new_records:
                    # ✨ 核心：调用数据管理器同步云端
                    new_df = pd.DataFrame(new_records)
                    st.toast("正在同步至云端数据库...")
                    updated_df = st.session_state.data_manager.save_data(new_df)
                    
                    # 更新本地显示
                    st.session_state.history_df = updated_df
                    
                    st.success(f"✅ 完成，更新 {len(new_records)} 篇")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.toast("✅ 完成，无新内容")

    render_results()

if __name__ == "__main__":
    main()
