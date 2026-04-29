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

st.title("🚗 HVFAN 报文分析 (JS广播防抖同步版)")
st.info("提示：此版本采用 JavaScript 底层广播机制，解决了多图表同步失效及卡顿问题。")

# ===================== 解析逻辑 =====================
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
    frame_re = re.compile(r'\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)')
    try:
        text_data = file_content.decode('utf-8', errors='ignore')
    except:
        text_data = file_content.decode('gbk', errors='ignore')
    lines = text_data.split('\n')
    for line in lines:
        m = frame_re.match(line)
        if m:
            t, cid = float(m.group('time')), int(m.group('id'), 16)
            raw = bytearray.fromhex(m.group('data').replace(' ', ''))
            try:
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

# ===================== Web 界面交互 =====================
db = load_dbc()

if not db:
    st.error(f"❌ 错误：未在服务器根目录找到 {DBC_FILENAME}。")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 正在深度解析报文数据...'):
                st.session_state.data_dict = process_asc(uploaded_file.read(), db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            st.warning("⚠️ 未识别到有效信号。")
        else:
            # --- 控制面板 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(data_dict.keys())
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp"])]
                selected_sigs = st.multiselect("📌 选择/删除信号", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
            with c2:
                sync_on = st.toggle("🔗 开启防抖同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                # 核心逻辑：构建 HTML + JS 广播环境
                # 我们不再使用 st.plotly_chart，而是将所有配置转化为 JSON 给到 JS 渲染
                charts_json = []
                for name in selected_sigs:
                    d = data_dict[name]
                    x, y = d['x'], d['y']
                    # 抽稀防止 JS 崩溃
                    if len(x) > 12000:
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    
                    charts_json.append({
                        "label": f"{d['label']} ({d['unit']})",
                        "x": x, "y": y, "unit": d['unit']
                    })

                # 构建 JS 代码
                js_sync_logic = """
                let timer = null;
                const chartIds = [];
                
                function broadcastRelayout(sourceId, eventData) {
                    if (!window.syncEnabled || !eventData['xaxis.range[0]']) return;
                    
                    // 防抖处理：避免高频采样导致的渲染过载
                    clearTimeout(timer);
                    timer = setTimeout(() => {
                        const update = {
                            'xaxis.range[0]': eventData['xaxis.range[0]'],
                            'xaxis.range[1]': eventData['xaxis.range[1]']
                        };
                        chartIds.forEach(id => {
                            if (id !== sourceId) {
                                Plotly.relayout(id, update);
                            }
                        });
                    }, 50); // 50ms 延迟防抖
                }
                """

                html_template = f"""
                <html>
                <head>
                    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                    <style>
                        .chart-container {{ margin-bottom: 20px; background: white; border-radius: 8px; padding: 10px; border: 1px solid #eee; }}
                        body {{ font-family: sans-serif; background-color: #f9f9f9; }}
                    </style>
                </head>
                <body>
                    <script>
                        window.syncEnabled = {str(sync_on).lower()};
                        {js_sync_logic}
                    </script>
                    <div id="wrapper">
                """

                for i, c in enumerate(charts_json):
                    div_id = f"chart_{i}"
                    fig_json = {
                        "data": [{"x": c['x'], "y": c['y'], "type": "scatter", "name": c['label'], "line": {"width": 1.5}}],
                        "layout": {
                            "title": c['label'], "height": 350, "template": "plotly_white",
                            "hovermode": "x unified" if show_measure else "closest",
                            "margin": {"l": 50, "r": 20, "t": 50, "b": 50},
                            "xaxis": {"showspikes": show_measure, "spikemode": "across", "spikedash": "dot"}
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
                
                # 计算组件高度：每个图表约 380px
                total_height = len(selected_sigs) * 385 + 50
                components.html(html_template, height=total_height, scrolling=False)

    if st.sidebar.button("♻️ 重新上传/刷新"):
        for key in list(st.session_state.keys()): del st.session_state[key]
        st.rerun()
    st.sidebar.caption("HVFAN Tool v17.0 | JS广播防抖版")
