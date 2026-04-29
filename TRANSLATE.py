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
            # 优先尝试 gbk (DBC 常用编码)
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

# ===================== 2. 状态保持与 UI (严格锁定 v17.6 逻辑) =====================
db = load_dbc()
st.title("🚗 HVFAN 报文分析 (v17.8 手机全兼容最终版)")

if not db:
    st.error(f"❌ 运行失败：未找到 DBC 文件 {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (.asc)", type=None)

    if uploaded_file is not None:
        file_key = f"v178_{uploaded_file.name}_{uploaded_file.size}"
        if 'full_data' not in st.session_state or st.session_state.get('last_key') != file_key:
            with st.spinner('🔍 正在深度解析...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.last_key = file_key
        
        full_data = st.session_state.full_data
        all_sig_names = sorted(full_data.keys())

        st.write("### 🛠️ 控制面板")
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp"])]
            selected_sigs = st.multiselect("📌 信号管理 (支持搜索/删除/恢复)", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
        with c2:
            sync_on = st.toggle("🔗 开启同步缩放", value=True)
        with c3:
            show_measure = st.toggle("📏 开启测量轴", value=True)

        if selected_sigs:
            # 性能策略：对 800V 电机高频信号进行精准抽稀，确保手机不闪退
            charts_to_render = []
            for i, name in enumerate(selected_sigs):
                d = full_data[name]
                x, y = d['x'], d['y']
                # 手机端流畅度阈值：6000点
                max_pts = 6000 
                if len(x) > max_pts:
                    step = len(x) // max_pts
                    x, y = x[::step], y[::step]
                charts_to_render.append({"id": f"chart_{i}", "title": f"{d['label']} ({d['unit']})", "x": x, "y": y})

            # --- 3. 手机端全兼容渲染引擎 (内核回滚 + 内存补丁) ---
            js_logic = f"""
            <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
            <div id="plot-container" style="width: 100%; overflow-x: hidden;"></div>
            <script>
                (function() {{
                    const dataPack = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const container = document.getElementById('plot-container');
                    
                    // 【内存加固】彻底回收旧图表显存
                    if (window.plotsPool) {{
                        window.plotsPool.forEach(id => {{ try {{ Plotly.purge(id); }} catch(e) {{}} }});
                    }}
                    window.plotsPool = [];
                    container.innerHTML = '';
                    window.isSyncing = false;

                    dataPack.forEach((item, idx) => {{
                        const plotDiv = document.createElement('div');
                        plotDiv.id = item.id;
                        // 【显示修复】强制设置宽度 100%，防止在手机 IFrame 中高度溃缩
                        plotDiv.style.cssText = "width: 100%; height: 350px; margin-bottom: 20px; background: #fff; border: 1px solid #eee; border-radius: 8px;";
                        container.appendChild(plotDiv);
                        window.plotsPool.push(item.id);

                        // 【加载优化】异步逐个加载，防止 CPU 瞬间过载导致手机白屏
                        setTimeout(() => {{
                            const config = {{ 
                                responsive: true, 
                                displaylogo: false, 
                                scrollZoom: true,
                                modeBarButtonsToRemove: ['select2d', 'lasso2d']
                            }};
                            
                            const layout = {{
                                title: {{ text: item.title, font: {{ size: 14 }} }},
                                margin: {{ l: 50, r: 20, t: 50, b: 40 }},
                                hovermode: "{'x unified' if show_measure else 'closest'}",
                                template: 'plotly_white',
                                xaxis: {{ 
                                    showspikes: {str(show_measure).lower()}, 
                                    spikemode: 'across', 
                                    spikedash: 'dot' 
                                }},
                                yaxis: {{ autorange: true, fixedrange: false }}
                            }};

                            Plotly.newPlot(item.id, [{{
                                x: item.x, y: item.y,
                                mode: 'lines',
                                line: {{ width: 2, color: '#174ea6' }},
                                name: item.title
                            }}], layout, config);

                            // 【功能锁定】同步缩放逻辑 (修复 v17.7 的转义错误)
                            if (syncEnabled) {{
                                plotDiv.on('plotly_relayout', function(eventData) {{
                                    if (window.isSyncing) return;
                                    window.isSyncing = true;
                                    
                                    let update = {{}};
                                    if (eventData['xaxis.range[0]']) {{
                                        update = {{ 'xaxis.range[0]': eventData['xaxis.range[0]'], 'xaxis.range[1]': eventData['xaxis.range[1]'] }};
                                    }} else if (eventData['xaxis.autorange']) {{
                                        update = {{ 'xaxis.autorange': true }};
                                    }}

                                    if (Object.keys(update).length > 0) {{
                                        const promises = window.plotsPool.map(pid => {{
                                            if (pid !== item.id) return Plotly.relayout(pid, update);
                                        }});
                                        Promise.all(promises).finally(() => {{ window.isSyncing = false; }});
                                    }} else {{
                                        window.isSyncing = false;
                                    }}
                                }});
                            }}
                        }}, idx * 120);
                    }});
                }})();
            </script>
            """
            # 锁定高度计算公式
            total_height = len(selected_sigs) * 375 + 150
            components.html(js_logic, height=total_height, scrolling=False)

    # 侧边栏辅助功能
    st.sidebar.markdown("---")
    if st.sidebar.button("♻️ 强制清理缓存并刷新"):
        st.session_state.clear()
        st.rerun()
