import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os

# ===================== 配置区域 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

st.title("🚗 HVFAN 报文自动化分析 (功能全集成版)")
st.info("提示：若手机端文件显示灰色，请点击“浏览”并从手机文件管理器中选择。")

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
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': msg.get_signal_by_name(s_n).unit or "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== Web 界面交互 =====================
db = load_dbc()

if not db:
    st.error(f"❌ 错误：未在服务器根目录找到 {DBC_FILENAME}。")
else:
    # 兼容手机：不限制 type
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (支持 .asc, .txt)", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 正在深度解析报文数据...'):
                file_bytes = uploaded_file.read()
                st.session_state.data_dict = process_asc(file_bytes, db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            st.warning("⚠️ 未识别到有效信号，请检查 DBC 匹配情况。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # --- 2. 核心控制面板 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                all_sig_names = sorted(data_dict.keys())
                # 默认显示包含关键字的信号（如电流、转速），方便用户
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp"])]
                selected_sigs = st.multiselect(
                    "📌 选择要显示的信号 (删除/恢复信号)", 
                    options=all_sig_names,
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)
            
            st.divider()

            # --- 3. 渲染选中的图表 ---
            if not selected_sigs:
                st.info("请在上方选择框中勾选想要查看的信号。")
            else:
                for name in selected_sigs:
                    d = data_dict[name]
                    s_label = d['label']
                    unit = d['unit']
                    
                    x, y = d['x'], d['y']
                    # 抽稀逻辑
                    if len(x) > 15000:
                        step = len(x) // 12000
                        x, y = x[::step], y[::step]

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=x, y=y, 
                        name=s_label, 
                        line=dict(width=1.5, color='#1f77b4'),
                        hovertemplate='%{y:.2f} ' + unit + '<extra></extra>'
                    ))
                    
                    fig.update_layout(
                        title=dict(text=f"信号: {s_label} ({unit})", font=dict(size=14)),
                        height=350,
                        margin=dict(l=10, r=10, t=50, b=10),
                        template="plotly_white",
                        hovermode="x unified" if show_measure else "closest",
                        xaxis=dict(
                            showgrid=True,
                            showspikes=show_measure,
                            spikethickness=1,
                            spikedash="dot",
                            spikemode="across",
                            matches='x' if sync_on else None # 同步缩放核心
                        ),
                        yaxis=dict(showgrid=True)
                    )
                    
                    st.plotly_chart(fig, use_container_width=True, config={'displaylogo': False})

    # 清除缓存按钮（手机端调试用）
    if st.sidebar.button("♻️ 重新上传/刷新"):
        for key in st.session_state.keys():
            del st.session_state[key]
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("HVFAN Tool v16.0")
