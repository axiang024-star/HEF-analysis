import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 核心配置 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

st.title("🚗 HVFAN 报文分析 (手机显示修复版)")

# ===================== 解析引擎 (已锁定) =====================
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
    # 锁定正则与多编码兼容逻辑
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t, cid = float(m.group('time')), int(m.group('id'), 16)
                raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                for s_n, s_v in decoded.items():
                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        data_dict[full_n] = {'x': [], 'y': [], 'unit': msg.get_signal_by_name(s_n).unit or "", 'label': s_n}
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== UI 交互 (已锁定) =====================
db = load_dbc()
if not db:
    st.error(f"❌ 缺失 DBC 文件")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 正在解析数据...'):
                st.session_state.data_dict = process_asc(uploaded_file.read(), db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            st.warning("⚠️ 未识别到有效信号")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # --- 控制面板 (锁定布局) ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(data_dict.keys())
                selected_sigs = st.multiselect("📌 信号管理", options=all_sig_names, default=[s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt"])] or all_sig_names[:2])
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_json = []
                for name in selected_sigs:
                    d = data_dict[name]
                    x, y = d['x'], d['y']
                    if len(x) > 12000: # 抽稀锁定
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    charts_json.append({"label": f"{d['label']} ({d['unit']})", "x": x, "y": y})

                # --- 核心显示修复：增加最小高度与 IFrame 强制高度 ---
                js_sync_logic = """
                let timer = null; const chartIds = [];
                function broadcastRelayout(sourceId, eventData) {
                    if (!window.syncEnabled) return;
                    let update = {};
                    if (eventData['xaxis.autorange'] === true) update = { 'xaxis.autorange': true };
                    else if (eventData['xaxis.range[0]']) update = { 'xaxis.range[0]': eventData['xaxis.range[0]'], 'xaxis.range[1]': eventData['xaxis.range[1]'] };
                    else return;
                    clearTimeout(timer);
                    timer = setTimeout(() => {
                        chartIds.forEach(id => { if (id !== sourceId) Plotly.relayout(id, update); });
                    }, 30); 
                }
                """

                html_template = f"""
                <html>
                <head>
                    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                    <style>
                        .chart-container {{ 
                            margin-bottom: 15px; 
                            background: white; 
                            border-radius: 8px; 
                            padding: 10px; 
                            border: 1px solid #eee;
                            min-height: 350px; /* 修复点：强制最小高度，防止手机端塌陷 */
                        }}
                        body {{ font-family: sans-serif; background-color: #fcfcfc; margin: 0; }}
                        #wrapper {{ padding: 5px; }}
                    </style>
                </head>
                <body>
                    <script>window.syncEnabled = {str(sync_on).lower()}; {js_sync_logic}</script>
                    <div id="wrapper">
                """

                for i, c in enumerate(charts_json):
                    div_id = f"chart_{i}"
                    fig_layout = {
                        "title": {"text": c['label'], "font": {"size": 14}},
                        "height": 350,
                        "template": "plotly_white",
                        "hovermode": "x unified" if show_measure else "closest",
                        "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
                        "xaxis": {"showgrid": True, "showspikes": show_measure, "spikemode": "across", "spikedash": "dot"}
                    }
                    html_template += f"""
                    <div class="chart-container"><div id="{div_id}" style="width:100%; height:350px;"></div></div>
                    <script>
                        chartIds.push("{div_id}");
                        Plotly.newPlot("{div_id}", [{{"x": {json.dumps(c['x'])}, "y": {json.dumps(c['y'])}, "type": "scatter", "mode": "lines", "line": {{"width": 1.5, "color": "#174ea6"}}}}], {json.dumps(fig_layout)}, {{responsive: true, displaylogo: false}});
                        document.getElementById("{div_id}").on('plotly_relayout', (data) => broadcastRelayout("{div_id}", data));
                    </script>
                    """
                html_template += "</div></body></html>"
                
                # 增加 100px 的冗余高度，确保滚动条不遮挡
                total_height = len(selected_sigs) * 395 + 100
                components.html(html_template, height=total_height, scrolling=False)

    if st.sidebar.button("♻️ 强制刷新"):
        st.session_state.clear()
        st.rerun()
