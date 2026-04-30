import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'HVFAN_Merged_Geely_Foton_FAW_Master.dbc'
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (硬解析补丁版) =====================
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
    # 增强版正则：兼容行首空格和 ID 间隙
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+'
        r'(?P<channel>\d+)\s+'
        r'(?P<id>[0-9A-Fa-f]+)x?\s+'
        r'Rx\s+d\s+'
        r'(?P<dlc>\d+)\s+'
        r'(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE | re.IGNORECASE
    )
    
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    # 【硬解析逻辑】寻找 PMS_HVFMCtrl 模板
    pms_msg_template = None
    try:
        pms_msg_template = db.get_message_by_name('PMS_HVFMCtrl')
    except:
        # 兜底：如果按名字找不到，尝试通过已知 ID 搜索
        for m in db.messages:
            if m.frame_id in [0x18FF4019, 0x18FF9027, 0x18748A00]:
                pms_msg_template = m
                break

    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                cid = int(m.group('id'), 16)
                
                # --- 硬解析拦截 ---
                # 无论 ID 是 18748A00 还是 18FF9027，统一指定到模板
                if cid in [0x18748A00, 0x18FF9027, 0x18FF4019, 0x98748A00, 0x98FF9027]:
                    msg = pms_msg_template
                else:
                    try:
                        msg = db.get_message_by_frame_id(cid)
                    except:
                        msg = db.get_message_by_frame_id(cid & 0x1FFFFFFF)
                
                if msg:
                    raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                    # 鲁棒性补齐
                    if len(raw) < msg.length:
                        raw.extend([0] * (msg.length - len(raw)))
                        
                    decoded = msg.decode(raw[:msg.length])
                    for s_n, s_v in decoded.items():
                        # 为了兼容性，如果是硬解析 ID，统一前缀，防止曲线断裂
                        if msg == pms_msg_template:
                            full_n = f"PMS_HVFMCtrl::{s_n}"
                        else:
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

# ===================== 3. UI 交互与逻辑控制 =====================
db = load_dbc()
st.title("🚗 HVFAN 综合分析系统 (硬解析整合版)")

if not db:
    st.error(f"❌ 缺失 DBC 文件: {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在应用硬解析策略并加载数据...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号，请检查报文 ID 是否符合 DBC 定义。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(full_data)} 个信号")

            # --- 控制面板 (V17.6 原版功能) ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                all_sig_names = sorted(full_data.keys())
                # 包含硬解析前缀的智能识别
                default_keywords = ["Spd", "Current", "Temp", "HVFMCtrl", "PMS"]
                default_sigs = [s for s in all_sig_names if any(k in s for k in default_keywords)]
                selected_sigs = st.multiselect(
                    "📌 信号管理", 
                    options=all_sig_names, 
                    default=default_sigs[:8] if default_sigs else all_sig_names[:2]
                )
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # V17.6 抽稀策略
                    limit = 10000 
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{abs(hash(name))}",
                        "title": f"{name} ({d['unit']})",
                        "x": x, "y": y
                    })

                # --- V17.6 增强版 JS 渲染引擎 ---
                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const chartsData = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const hoverMode = "{'x unified' if show_measure else 'closest'}";
                    const chartIds = [];
                    let isRelayouting = false;

                    const wrapper = document.getElementById('chart-wrapper');
                    
                    chartsData.forEach((data) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '20px';
                        div.style.height = '350px';
                        div.style.border = '1px solid #eee';
                        div.style.borderRadius = '8px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 14 }} }},
                            margin: {{ l: 50, r: 20, t: 50, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot', spikethickness: 1 }},
                            yaxis: {{ autorange: true }}
                        }};

                        Plotly.newPlot(data.id, [{{ x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: {{ width: 1.5 }} }}], layout, {{ responsive: true, displaylogo: false }});

                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (eventData) => {{
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {{}};
                                if (eventData['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = eventData['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = eventData['xaxis.range[1]'];
                                }} else if (eventData['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}

                                if (Object.keys(update).length > 0) {{
                                    const promises = chartIds.map(id => {{
                                        if (id !== data.id) return Plotly.relayout(id, update);
                                    }});
                                    Promise.all(promises).then(() => {{ isRelayouting = false; }});
                                }} else {{
                                    isRelayouting = false;
                                }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 375 + 50
                components.html(js_logic, height=render_height, scrolling=False)

    # 侧边栏
    if st.sidebar.button("♻️ 强制清除缓存并刷新"):
        st.session_state.clear()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("HVFAN Tool v18.1 | Hard-Parsing Integrated")
