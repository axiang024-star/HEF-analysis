import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 配置区域 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

st.title("🚗 HVFAN 报文分析 (手机/PC 兼容加强版)")
st.info("提示：此版本加固了手机端解析引擎。若依然无法识别，请尝试在 PC 端导出标准的 Vector ASC 格式。")

# ===================== 解析逻辑 (加固版) =====================
@st.cache_resource
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        try:
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk')
        except:
            return cantools.database.load_file(DBC_FILENAME, encoding='utf-8')
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 正则表达式优化：允许开头和结尾有更多灵活空格
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    # 编码尝试序列
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "." in text_data and "Rx" in text_data: # 简单校验是否成功解码
                break
        except:
            continue
            
    if not text_data:
        return {}

    # 统一换行符并按行处理，过滤空行
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                cid = int(m.group('id'), 16)
                raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                for s_n, s_v in decoded.items():
                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': msg.get_signal_by_name(s_n).unit or "", 
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== Web 界面交互 =====================
db = load_dbc()

if not db:
    st.error(f"❌ 错误：未找到 {DBC_FILENAME}。")
else:
    # 保持 type=None 以兼容手机浏览器
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (支持 .asc, .txt)", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 正在解析 800V 高频数据...'):
                # 读取内容并解析
                st.session_state.data_dict = process_asc(uploaded_file.read(), db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            # 手机端报错提示优化
            st.warning("⚠️ 未匹配到有效信号。请确认文件内容是否为标准的 ASC 报文格式（包含时间戳、ID、数据域）。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # --- 控制面板 (保留所有锁定功能) ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(data_dict.keys())
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp"])]
                selected_sigs = st.multiselect("📌 信号管理", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_json = []
                for name in selected_sigs:
                    d = data_dict[name]
                    x, y = d['x'], d['y']
                    if len(x) > 12000:
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    charts_json.append({"label": f"{d['label']} ({d['unit']})", "x": x, "y": y, "unit": d['unit']})

                # --- 锁定版 JS 逻辑 (同步 + Reset) ---
                js_sync_logic = """
                let timer = null;
                const chartIds = [];
                function broadcastRelayout(sourceId, eventData) {
                    if (!window.syncEnabled) return;
                    let update = {};
                    if (eventData['xaxis.autorange'] === true) {
                        update = { 'xaxis.autorange': true };
                    } else if (eventData['xaxis.range[0]']) {
                        update = { 'xaxis.range[0]': eventData['xaxis.range[0]'], 'xaxis.range[1]': eventData['xaxis.range[1]'] };
                    } else { return; }
                    clearTimeout(timer);
                    timer = setTimeout(() => {
                        chartIds.forEach(id => { if (id !== sourceId) Plotly.relayout(id, update); });
                    }, 30); 
                }
                """

                html_template = f"""
                <html>
                <head><script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <style>.chart-container {{ margin-bottom: 15px; background: white; border-radius: 8px; padding: 10px; border: 1px solid #eee; }}
                body {{ font-family: sans-serif; background-color: #fcfcfc; }}</style></head>
                <body><script>window.syncEnabled = {str(sync_on).lower()}; {js_sync_logic}</script><div id="wrapper">
                """
                for i, c in enumerate(charts_json):
                    div_id = f"chart_{i}"
                    fig_json = {
                        "data": [{"x": c['x'], "y": c['y'], "type": "scatter", "mode": "lines", "name": c['label'], "line": {"width": 1.5, "color": "#174ea6"}}],
                        "layout": {
                            "title": {"text": c['label'], "font": {"size": 14}}, "height": 350, "template": "plotly_white",
                            "hovermode": "x unified" if show_measure else "closest",
                            "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
                            "xaxis": {"showgrid": True, "showspikes": show_measure, "spikemode": "across", "spikedash": "dot"}
                        }
                    }
                    html_template += f"""
                    <div class="chart-container"><div id="{div_id}"></div></div>
                    <script>
                        chartIds.push("{div_id}");
                        Plotly.newPlot("{div_id}", {json.dumps(fig_json['data'])}, {json.dumps(fig_json['layout'])}, {{responsive: true, displaylogo: false}});
                        document.getElementById("{div_id}").on('plotly_relayout', (data) => broadcastRelayout("{div_id}", data));
                    </script>
                    """
                html_template += "</div></body></html>"
                components.html(html_template, height=len(selected_sigs) * 385 + 50, scrolling=False)

    if st.sidebar.button("♻️ 清除缓存并刷新"):
        for key in list(st.session_state.keys()): del st.session_state[key]
        st.rerun()
    st.sidebar.caption("HVFAN Tool v17.2 | Mobile Engine Hardened")
