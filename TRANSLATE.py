import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

@st.cache_resource(show_spinner=False)
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
            
    for m in frame_re.finditer(text_data):
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

# ===================== 2. UI 与 状态保持 (完全继承 v17.6) =====================
db = load_dbc()
st.title("🚗 HVFAN 报文分析 (v17.7.2 性能加固锁定版)")

if not db:
    st.error(f"❌ 缺失 DBC 文件: {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        # 锁定点：Session State 状态保持，确保增删信号不重新解析
        file_key = f"v1772_{uploaded_file.name}_{uploaded_file.size}"
        if 'full_data' not in st.session_state or st.session_state.get('last_key') != file_key:
            with st.spinner('🔍 正在深度解析...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.last_key = file_key
        
        full_data = st.session_state.full_data
        all_sig_names = sorted(full_data.keys())

        st.write("### 🛠️ 控制面板")
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            # 继承 v17.6 的信号管理逻辑
            default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp"])]
            selected_sigs = st.multiselect("📌 信号管理", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
        with c2:
            sync_on = st.toggle("🔗 开启同步缩放", value=True)
        with c3:
            show_measure = st.toggle("📏 开启测量轴 (Spike line)", value=True)

        if selected_sigs:
            # 数据抽稀逻辑 (继承 v17.6 兼容手机端)
            charts_to_render = []
            for i, name in enumerate(selected_sigs):
                d = full_data[name]
                x, y = d['x'], d['y']
                max_pts = 6000 
                if len(x) > max_pts:
                    step = len(x) // max_pts
                    x, y = x[::step], y[::step]
                charts_to_render.append({"id": f"chart_{i}", "title": f"{d['label']} ({d['unit']})", "x": x, "y": y})

            # --- 3. 内存回收版渲染引擎 (修复转义报错) ---
            # 使用 json.dumps 传递变量，并在 JS 内部处理逻辑，避免 Python 转义冲突
            js_logic = f"""
            <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
            <div id="main-wrapper"></div>
            <script>
                (function() {{
                    const dataPack = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const hoverMode = "{'x unified' if show_measure else 'closest'}";
                    const showSpikes = {str(show_measure).lower()};
                    const wrapper = document.getElementById('main-wrapper');
                    
                    // 彻底清理：防止手机内存泄漏
                    if (window.activePlots) {{
                        window.activePlots.forEach(id => {{
                            try {{ Plotly.purge(id); }} catch(e) {{}}
                        }});
                    }}
                    window.activePlots = [];
                    wrapper.innerHTML = ''; 

                    window.isSyncing = false;

                    dataPack.forEach((item, idx) => {{
                        const div = document.createElement('div');
                        div.id = item.id;
                        div.style.cssText = "height:350px; margin-bottom:20px; border:1px solid #eee; border-radius:8px; background:white;";
                        wrapper.appendChild(div);
                        window.activePlots.push(item.id);

                        setTimeout(() => {{
                            const trace = {{
                                x: item.x, y: item.y,
                                type: 'scatter', mode: 'lines',
                                line: {{ width: 2, color: '#174ea6' }},
                                name: item.title
                            }};

                            const layout = {{
                                title: {{ text: item.title, font: {{ size: 14 }} }},
                                margin: {{ l: 50, r: 20, t: 50, b: 40 }},
                                hovermode: hoverMode,
                                template: 'plotly_white',
                                xaxis: {{ 
                                    showspikes: showSpikes, 
                                    spikemode: 'across', 
                                    spikedash: 'dot' 
                                }},
                                yaxis: {{ autorange: true }}
                            }};

                            Plotly.newPlot(item.id, [trace], layout, {{ responsive: true, displaylogo: false }});

                            // 同步逻辑：严格校对大括号转义
                            if (syncEnabled) {{
                                document.getElementById(item.id).on('plotly_relayout', function(ed) {{
                                    if (window.isSyncing) return;
                                    window.isSyncing = true;
                                    
                                    let update = {{}};
                                    if (ed['xaxis.range[0]']) {{
                                        update = {{ 'xaxis.range[0]': ed['xaxis.range[0]'], 'xaxis.range[1]': ed['xaxis.range[1]'] }};
                                    }} else if (ed['xaxis.autorange']) {{
                                        update = {{ 'xaxis.autorange': true }};
                                    }}

                                    if (Object.keys(update).length > 0) {{
                                        const syncPromises = window.activePlots.map(pid => {{
                                            if (pid !== item.id) return Plotly.relayout(pid, update);
                                        }});
                                        Promise.all(syncPromises).then(() => {{ window.isSyncing = false; }});
                                    }} else {{
                                        window.isSyncing = false;
                                    }}
                                }});
                            }}
                        }}, idx * 100);
                    }});
                }})();
            </script>
            """
            render_height = len(selected_sigs) * 375 + 100
            components.html(js_logic, height=render_height, scrolling=False)

    if st.sidebar.button("♻️ 强制重置内存"):
        st.session_state.clear()
        st.rerun()
