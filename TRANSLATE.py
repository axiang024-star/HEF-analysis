import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 核心配置 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

st.title("🚗 HVFAN 报文分析 (手机渲染最终修复版)")

# ===================== 解析引擎 (锁定版) =====================
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

# ===================== UI 交互 (锁定版) =====================
db = load_dbc()
if not db:
    st.error(f"❌ 缺失 DBC 文件")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 解析中...'):
                st.session_state.data_dict = process_asc(uploaded_file.read(), db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            st.warning("⚠️ 未识别到有效信号")
        else:
            st.success(f"✅ 解析成功！已识别 {len(data_dict)} 个信号")

            # --- 控制面板 (锁定) ---
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
                    if len(x) > 8000: # 手机端进一步调低阈值确保渲染成功
                        step = len(x) // 8000
                        x, y = x[::step], y[::step]
                    charts_json.append({"label": f"{d['label']} ({d['unit']})", "x": x, "y": y})

                # --- 终极修复：使用双大括号 {{}} 转义 f-string ---
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
                    }, 50); 
                }
                """

                html_template = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                    <style>
                        .chart-container {{ 
                            margin-bottom: 20px; background: white; 
                            border-radius: 8px; padding: 10px; border: 1px solid #ddd;
                            height: 350px; width: 95%; margin: 0 auto;
                        }}
                        body {{ font-family: sans-serif; background-color: #ffffff; margin: 0; }}
                    </style>
                </head>
                <body>
                    <div id="wrapper">
                """

                for i, c in enumerate(charts_json):
                    div_id = f"chart_{i}"
                    fig_layout = {
                        "title": {"text": c['label'], "font": {"size": 16}, "y": 0.95},
                        "autosize": True, "height": 350, "template": "plotly_white",
                        "hovermode": "x unified" if show_measure else "closest",
                        "margin": {"l": 40, "r": 20, "t": 60, "b": 40},
                        "xaxis": {"showgrid": True, "showspikes": show_measure, "spikemode": "across", "spikedash": "dot"}
                    }
                    # 关键修复点：JS 内部的大括号全部转义为 {{ }}
                    html_template += f"""
                    <div class="chart-container"><div id="{div_id}" style="width:100%; height:100%;"></div></div>
                    <script>
                        chartIds.push("{div_id}");
                        (function() {{
                            const data = [{{
                                x: {json.dumps(c['x'])}, 
                                y: {json.dumps(c['y'])}, 
                                type: "scatter", mode: "lines", 
                                line: {{width: 2, color: "#174ea6"}}
                            }}];
                            const layout = {json.dumps(fig_layout)};
                            setTimeout(() => {{
                                Plotly.newPlot("{div_id}", data, layout, {{responsive: true, displaylogo: false}});
                                document.getElementById("{div_id}").on('plotly_relayout', (d) => broadcastRelayout("{div_id}", d));
                            }}, {i * 150});
                        }})();
                    </script>
                    """

                html_template += f"""
                    </div>
                    <script>
                        window.syncEnabled = {str(sync_on).lower()};
                        {js_sync_logic}
                    </script>
                </body>
                </html>
                """
                
                total_height = len(selected_sigs) * 380 + 100
                components.html(html_template, height=total_height, scrolling=False)

    if st.sidebar.button("♻️ 强制刷新"):
        st.session_state.clear()
        st.rerun()
