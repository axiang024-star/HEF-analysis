import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (多 DBC 自动合并) =====================
@st.cache_resource
def load_combined_db():
    # 自动扫描当前目录
    dbc_files = [f for f in os.listdir('.') if f.lower().endswith('.dbc')]
    if not dbc_files:
        return None, []

    combined_db = cantools.database.Database()
    loaded_successfully = []
    for dbc_file in dbc_files:
        try:
            try:
                temp_db = cantools.database.load_file(dbc_file, encoding='gbk')
            except:
                temp_db = cantools.database.load_file(dbc_file, encoding='utf-8')
            for msg in temp_db.messages:
                combined_db.add_message(msg)
            loaded_successfully.append(dbc_file)
        except: continue
    return combined_db, loaded_successfully

def process_asc(file_content, db):
    data_dict = {}
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    text_data = file_content.decode('utf-8', errors='ignore')
    
    for line in text_data.splitlines():
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

# ===================== 3. UI 交互 (v17.6 强力锁定版) =====================
db, loaded_dbcs = load_combined_db()
st.title("🚗 HVFAN 报文分析 (修复版)")

st.sidebar.header("📁 已加载协议库")
if loaded_dbcs:
    for f in loaded_dbcs: st.sidebar.caption(f"✅ {f}")
else:
    st.sidebar.error("❌ 未检测到DBC文件，请检查根目录")

if db:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)
    if uploaded_file:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在解析...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data
        if full_data:
            st.write("### 🛠️ 控制面板")
            # 信号选择、同步缩放等 UI 逻辑保持不变...
            selected_sigs = st.multiselect("📌 信号管理", options=sorted(full_data.keys()))
            
            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    # v17.6 抽稀策略
                    x, y = d['x'], d['y']
                    if len(x) > 10000:
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    charts_to_render.append({"id": f"chart_{selected_sigs.index(name)}", "title": name, "x": x, "y": y})

                # --- 4. JS 渲染核心 (全量转义解决 SyntaxError) ---
                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const data = {json.dumps(charts_to_render)};
                    const wrapper = document.getElementById('chart-wrapper');
                    data.forEach((d) => {{
                        const container = document.createElement('div');
                        container.id = d.id;
                        container.style.height = '350px';
                        wrapper.appendChild(container);
                        
                        const trace = {{ x: d.x, y: d.y, type: 'scatter', mode: 'lines' }};
                        const layout = {{ title: d.title, margin: {{ t: 30 }} }};
                        Plotly.newPlot(d.id, [trace], layout, {{ responsive: true }});
                    }});
                </script>
                """
                components.html(js_logic, height=len(selected_sigs)*360+50)

if st.sidebar.button("♻️ 强制清除缓存并刷新"):
    st.session_state.clear()
    st.rerun()
