import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统 (多DBC终极修复版)", layout="wide")

# ===================== 2. 解析引擎 (多DBC自动合并) =====================
@st.cache_resource
def load_combined_db():
    """扫描目录并合并所有DBC"""
    # 针对 Streamlit Cloud 优化路径获取
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dbc_files = [f for f in os.listdir(base_dir) if f.lower().endswith('.dbc')]
    
    if not dbc_files:
        return None, []

    combined_db = cantools.database.Database()
    loaded_successfully = []

    for dbc_file in dbc_files:
        try:
            full_path = os.path.join(base_dir, dbc_file)
            # 尝试多种编码加载
            for enc in ['gbk', 'utf-8', 'latin-1']:
                try:
                    temp_db = cantools.database.load_file(full_path, encoding=enc)
                    break
                except:
                    continue
            
            for msg in temp_db.messages:
                combined_db.add_message(msg)
            loaded_successfully.append(dbc_file)
        except:
            continue
            
    return combined_db, loaded_successfully

def process_asc(file_content, db):
    data_dict = {}
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    # 解码内容
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
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': msg.get_signal_by_name(s_n).unit or "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. UI 交互与强力锁定逻辑 =====================
db, loaded_dbcs = load_combined_db()
st.title("🚗 HVFAN 报文分析 (多DBC合并修复版)")

st.sidebar.header("📁 已加载协议库")
if loaded_dbcs:
    for f in loaded_dbcs:
        st.sidebar.caption(f"✅ {f}")
else:
    st.sidebar.error("❌ 未检测到DBC文件，请确保DBC文件在仓库根目录")

if db:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (.asc)", type=None)

    if uploaded_file:
        # v17.6 核心强力锁定：防止手机端重复触发解析
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在深度解析报文...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能匹配到信号，请确认DBC协议是否正确。")
        else:
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                selected_sigs = st.multiselect("📌 信号管理", options=sorted(full_data.keys()))
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # v17.6 性能策略：1万点抽稀
                    if len(x) > 10000:
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    charts_to_render.append({"id": f"chart_{selected_sigs.index(name)}", "title": name, "x": x, "y": y})

                # ===================== 4. 彻底修复 SyntaxError 的渲染引擎 =====================
                # 方案：不使用 f-string，改用字符串 replace 注入变量，规避大括号冲突
                js_template = """
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const chartsData = __DATA__;
                    const syncEnabled = __SYNC__;
                    const hoverMode = "__HOVER__";
                    const spikeMode = __SPIKE__;
                    const chartIds = [];
                    let isRelayouting = false;

                    const wrapper = document.getElementById('chart-wrapper');
                    chartsData.forEach((data) => {
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '20px';
                        div.style.height = '350px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const trace = {
                            x: data.x, y: data.y,
                            type: 'scatter', mode: 'lines',
                            line: { width: 2, color: '#174ea6' },
                            name: data.title
                        };

                        const layout = {
                            title: { text: data.title, font: { size: 14 } },
                            margin: { l: 50, r: 20, t: 50, b: 40 },
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: { showspikes: spikeMode, spikemode: 'across', spikedash: 'dot' }
                        };

                        Plotly.newPlot(data.id, [trace], layout, { responsive: true, displaylogo: false });

                        if (syncEnabled) {
                            document.getElementById(data.id).on('plotly_relayout', (eventData) => {
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {};
                                if (eventData['xaxis.range[0]']) {
                                    update['xaxis.range[0]'] = eventData['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = eventData['xaxis.range[1]'];
                                } else if (eventData['xaxis.autorange']) {
                                    update['xaxis.autorange'] = true;
                                }

                                if (Object.keys(update).length > 0) {
                                    const promises = chartIds.map(id => id !== data.id ? Plotly.relayout(id, update) : null);
                                    Promise.all(promises).then(() => { isRelayouting = false; });
                                } else { isRelayouting = false; }
                            });
                        }
                    });
                </script>
                """
                # 注入数据并进行替换
                final_js = js_template.replace("__DATA__", json.dumps(charts_to_render))
                final_js = final_js.replace("__SYNC__", str(sync_on).lower())
                final_js = final_js.replace("__HOVER__", 'x unified' if show_measure else 'closest')
                final_js = final_js.replace("__SPIKE__", str(show_measure).lower())

                render_height = len(selected_sigs) * 375 + 50
                components.html(final_js, height=render_height, scrolling=False)

# 侧边栏辅助功能
if st.sidebar.button("♻️ 强制清除缓存并刷新"):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("HVFAN Tool v17.6 | Multi-DBC Fixed")
