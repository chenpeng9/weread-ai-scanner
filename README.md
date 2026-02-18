# 💀 WeRead AI (公众号毒舌审计师)

> **“只读干货，拒绝韭菜。”** —— 基于 Google Gemini 的公众号文章智能审计系统。

## 📖 项目简介

**WeRead AI** 是一个专为高净值投资者、行业研究员和“数字游民”设计的公众号信息过滤器。

在这个信息过载的时代，公众号充斥着软广、带货、情绪宣泄和无效的“正确的废话”。本项目利用 **Google Gemini** 大模型的长文本分析能力，化身为一位**“毒舌做空机构分析师”**，对订阅号文章进行全量扫描和深度审计。

它不只是总结，它负责**批判**。

## ✨ 核心功能

* **🕵️‍♂️ 毒舌审计 (Toxic Audit)**：AI 人格设定为“浑水调研”分析师，对带货、卖课、软广零容忍（直接打 0-2 分），精准识别“割韭菜”行为。
* **⚡ 极速全量扫描**：支持批量导入上百个公众号，利用并发逻辑快速获取今日/昨日的最新文章。
* **🛡️ 交互丝滑**：基于 Streamlit Form 的批量管理界面，拒绝勾选回弹，支持一键全选/反选。
* **📊 自动去重与增量更新**：自动过滤已读文章，节省 Token，避免重复劳动。
* **📥 研报级导出**：支持导出格式完美的 Excel 报表（基于 `xlsxwriter`），自动调整列宽，方便存档。
* **☁️ 云端原生**：完美适配 Streamlit Community Cloud，无需本地算力，手机/iPad 随时访问。

## 🛠️ 技术栈

* **Frontend**: [Streamlit]() (极简 Python Web 框架)
* **AI Engine**: Google Gemini 1.5 / 3 Flash (通过 `google-generativeai` 或 Rest API)
* **Data Processing**: Pandas, OpenPyXL, XlsxWriter
* **Data Source**: WxRank API (需自行申请 Key)

## 🚀 快速开始 (本地运行)

### 1. 克隆项目

```bash
git clone https://github.com/your-username/weread-ai.git
cd weread-ai

```

### 2. 安装依赖

建议使用 Python 3.9+ 环境：

```bash
pip install -r requirements.txt

```

### 3. 配置密钥

在项目根目录创建 `.streamlit/secrets.toml` 文件（不要上传到 GitHub！）：

```toml
# .streamlit/secrets.toml

# WxRank 数据源 Key
WX_KEY = "你的_WxRank_API_Key"

# Google Gemini API Key
GEMINI_KEY = "你的_Gemini_API_Key"

```

### 4. 启动应用

```bash
streamlit run app.py

```

## ☁️ 部署到 Streamlit Cloud (推荐)

本项目专为云端设计，无需购买服务器，完全免费。

1. 将代码 push 到 GitHub。
2. 登录 [Streamlit Cloud]()。
3. 点击 **New app**，选择你的仓库。
4. **关键步骤**：在部署界面的 **Advanced settings -> Secrets** 中填入你的 API Key：
```toml
WX_KEY = "xxx"
GEMINI_KEY = "xxx"

```


5. 点击 **Deploy**，等待 1 分钟即可获得专属访问链接。

## 📂 文件结构说明

* `app.py`: 主程序逻辑（包含 UI、AI 分析、数据处理）。
* `requirements.txt`: 项目依赖库列表。
* `README.md`: 项目说明文档。

## 📝 导入格式说明 (Excel XML)

支持导入 Excel 2003 XML 格式的公众号列表。系统会自动跳过第一行表头。
有效列需包含：`公众号名称` 和 `公众号ID`。

## ⚖️ 免责声明

* 本项目仅供学习和个人研究使用。
* 请勿用于商业用途或大规模爬取，遵守微信及数据源平台的使用规范。
* AI 观点仅供参考，不构成投资建议。

---

**Enjoy your Alpha, ignore the Noise.** 🚀
