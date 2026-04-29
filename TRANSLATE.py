import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统 (全平台修复版)", layout="wide")

# ===================== 2. 解析引擎 (多库隔离与自适应版) =====================

@st.cache_resource
def load_all_dbcs_map():
    """
    自动扫描当前脚本同级目录下所有的 .dbc 文件，并返回一个字典 {文件名: 数据库对象}
    """
    db_map = {}
    # 获取脚本绝对路径，确保在云端环境下也能精准定位
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 扫描目录下所有以 .dbc 结尾的文件
    dbc_files = [f for f in os.listdir(base_dir) if f.lower().endswith('.dbc')]
    
    for file_name in dbc_files:
        file_path = os.path.join(base_dir, file_name)
        try:
            # 优先尝试 GBK，再尝试 UTF-8
            try:
                db = cantools.database.load_file(file_path, encoding='gbk')
            except:
                db = cantools.database.load_file(file_path, encoding='utf-8')
            db_map[file_name] = db
        except Exception as e:
            st.error(f"解析仓库文件 {file_name} 失败: {e}")
            
    return db_map

def process_asc_multi_db(file_content, db_map):
    """
    基于多库并行解析 ASC 报文，防止 ID 冲突导致的信号缺失
    """
    data_dict = {}
    # Vector ASC 标准正则
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    # 多编码尝试读取 ASC 内容
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
                t = float(m.group('time'))
                cid = int(m.group('id'), 16)
                raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                
                # 核心逻辑：遍历每一个 DBC，尝试用其定义进行解析
                for dbc_name, db in db_map.items():
                    try:
                        msg = db.get_message_by_frame_id(cid)
                        # 如果 DLC 长度不足以解析，某些库会报错，此处进行隔离
                        decoded = msg.decode(raw)
                        for s_n, s_v in decoded.items():
                            # 使用 [文件名] 作为前缀，解决冲突
                            full_n = f"[{dbc_name}] {msg.name}::{s_n}"
                            if full_n not in data_dict:
                                data_dict[full_n] = {
                                    'x': [], 'y': [], 
                                    'unit': msg.get_signal_by_name(s_n).unit or "",
                                    'label': s_n
                                }
                            data_dict[full_n]['x'].append(t)
                            data_dict[full_n]['y'].append(s_v)
                    except:
                        # 该 ID 在此 DBC 中不存在或长度不匹配，跳过
                        continue
            except: continue
    return data_dict

# ===================== 3. UI 交互逻辑 =====================

# 1. 自动加载本地协议库映射
db_map = load_all_dbcs_map()

st.title("🚗 HVFAN 报文分析 (多协议全兼容版)")

# 侧边栏：状态指示与备用方案
with st.sidebar:
    st.header("📂 协议库引擎")
    if not db_map:
        st.warning("⚠️ 仓库内未检测到 DBC 文件")
    else:
        st.success(f"✅ 已激活 {len(db_map)} 个独立协议库")
        with st.expander("已加载清单"):
            for name in db_map.keys():
                st.caption(f"📄 {name}")
    
    st.divider()
    # 手动上传支持（解决手机端灰色不可选）
    st.write("🔧 **手动补充协议**")
    manual_files = st.file_uploader("若有新DBC可在此叠加", type=None, accept_multiple_files=True)
    if manual_files:
        for m_file in manual_files:
            try:
                m_content = m_file.getvalue().decode('gbk', errors='ignore')
                m_db = cantools.database.load_string(m_content)
                db_map[m_file.name] = m_db
                st.sidebar.info(f"叠加成功: {m_file.name}")
            except:
                st.sidebar.error(f"解析 {m_file.name} 失败")

    if st.button("♻️ 强制清除缓存并刷新"):
        st.session_state.clear()
        st.rerun()
    st.caption("v17.9 | Multi-Namespace Fixed")

# 主界面：数据上传
if not db_map:
    st.info("💡 请将 DBC 文件上传至 GitHub 仓库或在左侧手动添加。")
else:
    # 手机端兼容：type=None 允许所有文件被选中
    uploaded_file = st.file_uploader("📂 选择并上传路测报文 (.asc)", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在多维匹配信号中...'):
                st.session_state.full_data = process_asc_multi_db(uploaded_file.read(), db_map)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.error("⚠️ 未能从当前已加载的协议中匹配到任何信号。请检查 DBC 是否正确。")
        else:
            # 统计并展示解析结果
            st.success(f"✅ 解析成功！识别到 {len(full_data)} 个信号")

            # 🛠️ 控制面板
            st.write("### 🛠️ 数据筛选")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(full_data.keys())
                # 智能识别关键词
                keywords = ["Spd", "Current", "Volt", "Temp", "Duty", "Fan"]
                default_sigs = [s for s in all_sig_names if any(k in s for k in keywords)]
                selected_sigs = st.multiselect("📌 信号选择", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 动态抽稀：手机端性能优化
                    limit = 8000 
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{selected_sigs.index(name)}",
                        "title": name,
                        "unit": d['unit'],
                        "x": x, "y": y
                    })

                # Plotly JS 高性能渲染
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
                    
                    chartsData.forEach((data, index) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '25px';
                        div.style.height = '350px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const layout = {{
                            title: {{ text: data.title + (data.unit ? ' ('+data.unit+')' : ''), font: {{ size: 13 }} }},
                            margin: {{ l: 55, r: 25, t: 50, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: {str(show_measure).lower()}, spikemode: 'across', spikesnap: 'cursor' }},
                            yaxis: {{ autorange: true, tickformat: '.2f' }}
                        }};

                        Plotly.newPlot(data.id, [{{ 
                            x: data.x, y: data.y, 
                            type: 'scatter', mode: 'lines', 
                            line: {{ width: 1.8, color: '#174ea6' }},
                            name: 'Value' 
                        }}], layout, {{ responsive: true, displaylogo: false }});

                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (ed) => {{
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {{}};
                                if (ed['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = ed['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = ed['xaxis.range[1]'];
                                }} else if (ed['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}
                                if (Object.keys(update).length > 0) {{
                                    const ps = chartIds.map(id => id !== data.id ? Plotly.relayout(id, update) : null);
                                    Promise.all(ps).then(() => {{ isRelayouting = false; }});
                                }} else {{ isRelayouting = false; }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 380 + 100
                components.html(js_logic, height=render_height, scrolling=False)
