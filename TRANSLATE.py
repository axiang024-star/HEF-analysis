import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'HVFAN_Merged_Geely_Foton_FAW_Master.dbc'
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (增强 ID 兼容性) =====================
@st.cache_resource
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        try:
            # 尝试加载 DBC
            db = cantools.database.load_file(DBC_FILENAME, encoding='gbk')
            return db
        except:
            return cantools.database.load_file(DBC_FILENAME, encoding='utf-8')
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 预编译正则提高效率
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    
    # 建立 ID 映射缓存，避免在循环中重复计算
    # 将 DBC 中所有 ID 强制转为 29 位标准格式 (mask 0x1FFFFFFF)
    id_map = { (m.frame_id & 0x1FFFFFFF): m.frame_id for m in db.messages }

    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                raw_cid = int(m.group('id'), 16)
                
                # 关键修复：强制取 29 位有效 ID
                # 这样 18FF9027 就能匹配到 DBC 里的 2566885415 (0x99401C17)
                standard_id = raw_cid & 0x1FFFFFFF
                
                if standard_id in id_map:
                    target_id = id_map[standard_id]
                    msg = db.get_message_by_frame_id(target_id)
                    
                    # 准备数据流
                    hex_data = m.group('data').replace(' ', '')
                    raw = bytearray.fromhex(hex_data)
                    
                    # 补齐长度：防止因为 DLC 不足导致的解码失败
                    if len(raw) < msg.length:
                        raw.extend([0] * (msg.length - len(raw)))
                    
                    decoded = msg.decode(raw)
                    
                    for s_n, s_v in decoded.items():
                        full_n = f"{msg.name}::{s_n}"
                        if full_n not in data_dict:
                            # 提取单位和标签 [cite: 1, 4, 6]
                            sig_obj = msg.get_signal_by_name(s_n)
                            data_dict[full_n] = {
                                'x': [], 'y': [], 
                                'unit': sig_obj.unit or "",
                                'label': s_n
                            }
                        data_dict[full_n]['x'].append(t)
                        data_dict[full_n]['y'].append(s_v)
            except Exception:
                continue
    return data_dict

# ===================== 3. UI 交互逻辑 =====================
db = load_dbc()
st.title("🚗 HVFAN 报文分析 (ID 兼容增强版)")

if not db:
    st.error(f"❌ 缺失 DBC 文件: {DBC_FILENAME}")
else:
    # 侧边栏信息展示
    with st.sidebar:
        st.header("DBC 状态")
        st.info(f"已加载: {len(db.messages)} 条消息")
        if st.button("♻️ 强制刷新"):
            st.session_state.clear()
            st.rerun()

    uploaded_file = st.file_uploader("📂 上传 ASC 报文文件", type=['asc', 'txt'])

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在解析报文并对齐 ID...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号。请确认 ASC ID 是否为 18FF9027x 等扩展格式。")
        else:
            st.success(f"✅ 解析成功！已识别信号: {len(full_data)}")

            # 控制面板
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                all_sig_names = sorted(full_data.keys())
                # 默认显示关键信号：转速、电流、电压、温度 
                default_keywords = ["Spd", "Current", "IInput", "UInput", "Temp", "Volt"]
                default_sigs = [s for s in all_sig_names if any(k in s for k in default_keywords)]
                selected_sigs = st.multiselect(
                    "📌 选择信号", 
                    options=all_sig_names, 
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            with c2:
                sync_on = st.toggle("🔗 同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 动态抽稀（PC端保持流畅，手机端不卡顿）
                    limit = 12000
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{hash(name)}", # 使用 hash 保证唯一性
                        "title": f"{name} ({d['unit']})",
                        "x": x,
                        "y": y
                    })

                # JS 渲染引擎 (包含同步缩放逻辑)
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
                        div.style.marginBottom = '15px';
                        div.style.height = '350px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const trace = {{
                            x: data.x, y: data.y,
                            type: 'scatter', mode: 'lines',
                            line: {{ width: 1.5, color: '#1f77b4' }},
                            name: data.title
                        }};

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 13 }} }},
                            margin: {{ l: 50, r: 20, t: 40, b: 30 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot' }},
                            yaxis: {{ autorange: true }}
                        }};

                        Plotly.newPlot(data.id, [trace], layout, {{ responsive: true, displaylogo: false }});

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
                                    Promise.all(promises).finally(() => {{ isRelayouting = false; }});
                                }} else {{ isRelayouting = false; }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 370 + 50
                components.html(js_logic, height=render_height, scrolling=False)

    st.sidebar.divider()
    st.sidebar.caption("HVFAN Tool v18.0 | ID Patch Integrated")
