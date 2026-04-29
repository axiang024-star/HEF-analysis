import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统 (多DBC终极修复版)", layout="wide")

# ===================== 2. 解析引擎 (增强型多DBC加载) =====================
@st.cache_resource
def load_combined_db():
    # 获取当前脚本所在目录
    base_path = os.path.dirname(__file__) if "__file__" in locals() else "."
    # 扫描所有 .dbc 文件
    dbc_files = [f for f in os.listdir(base_path) if f.lower().endswith('.dbc')]
    
    if not dbc_files:
        return None, []

    combined_db = cantools.database.Database()
    loaded_successfully = []

    for dbc_file in dbc_files:
        try:
            full_path = os.path.join(base_path, dbc_file)
            # 兼容多种编码加载
            for encoding in ['gbk', 'utf-8', 'latin-1']:
                try:
                    temp_db = cantools.database.load_file(full_path, encoding=encoding)
                    break
                except:
                    continue
            
            # 合并消息
            for msg in temp_db.messages:
                combined_db.add_message(msg)
            loaded_successfully.append(dbc_file)
        except Exception as e:
            continue
            
    return combined_db, loaded_successfully

def process_asc(file_content, db):
    data_dict = {}
    # 标准 Vector ASC 正则
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    text_data = file_content.decode('utf-8', errors='ignore')
    lines = text_data.splitlines()
    
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

# ===================== 3. UI 交互 =====================
db, loaded_dbcs = load_combined_db()
st.title("🚗 HVFAN 报文分析 (多DBC合并修复版)")

# 侧边栏：显示加载状态
st.sidebar.header("📁 已加载协议库")
if loaded_dbcs:
    for f in loaded_dbcs:
        st.sidebar.caption(f"✅ {f}")
else:
    st.sidebar.error("❌ 未检测到DBC文件，请确保DBC文件在仓库根目录")

if not db:
    st.error("❌ 无法解析任何 DBC 文件。请检查文件格式。")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (.asc)", type=None)

    if uploaded_file:
        # v17.6 强力锁定：基于特征锁定，防止手机端重复解析
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在深度解析报文...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 解析结果为空。请确认上传的 ASC 报文内容正确，且 ID 在协议库范围内。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(full_data)} 个信号")

            # --- 控制面板 ---
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                selected_sigs = st.multiselect("📌 选择信号绘制图表", options=sorted(full_data.keys()))
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 手机端抽稀策略 (v17.6 核心优化)
                    if len(x) > 10000:
                        step = len(x) // 10000
                        x, y = x[::step], y[::step]
                    charts_to_render.append({"id": f"chart_{selected_sigs.index(name)}", "title": name, "x": x, "y": y})

                # --- 4. 彻底解决报错：使用非 f-string 注入 JS 逻辑 ---
                charts_json = json.dumps(charts_to_render)
                sync_val = str(sync_on).lower()
                measure_val = 'x unified' if show_measure else 'closest'
                spike_val = str(show_measure).lower()

                # 将 JS 逻辑作为普通字符串，通过 replace 填入变量，规避 f-string 大括号冲突
                raw_js = """
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const chartsData = CH_DATA;
                    const syncEnabled = SYNC_VAL;
                    const hoverMode = "HOVER_VAL";
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

                        const trace = { x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: { width: 2, color: '#174ea6' } };
                        const layout = { 
                            title: { text: data.title, font: { size: 14 } },
                            margin: { l: 50, r: 20, t: 50, b: 40 },
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: { showspikes: SPIKE_VAL, spikemode: 'across', spikedash: 'dot' }
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
                                    const ps = chartIds.map(id => id !== data.id ? Plotly.relayout(id, update) : null);
                                    Promise.all(ps).then(() => { isRelayouting = false; });
                                } else { isRelayouting = false; }
                            });
                        }
                    });
                </script>
                """
                # 注入变量
                final_js = raw_js.replace("CH_DATA", charts_json)\
                                 .replace("SYNC_VAL", sync_val)\
                                 .replace("HOVER_VAL", measure_val)\
                                 .replace("SPIKE_VAL", spike_val)

                render_height = len(selected_sigs) * 375 + 50
                components.html(final_js, height=render_height, scrolling=False)

# 侧边栏清理
if st.sidebar.button("♻️ 强制清除缓存并刷新"):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("HVFAN Tool v17.6 | Multi-DBC Ultimate Fixed")
