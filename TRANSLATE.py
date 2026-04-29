import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 配置区域 =====================
# 将 DBC 文件放在脚本同级目录下
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

# 页面基础配置：保留 wide 模式
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

st.title("🚗 HVFAN 报文自动化分析 (手机/PC 功能版)")
st.info("只需上传 .asc 文件，即可自动基于内置 DBC 解析信号波形。")

# ===================== 解析逻辑 =====================
@st.cache_resource # 缓存 DBC 加载，提高性能
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        try:
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk')
        except:
            return cantools.database.load_file(DBC_FILENAME, encoding='utf-8')
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 正则表达式适配 ASC 格式
    frame_re = re.compile(r'\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)')
    
    # 将文件内容转为文本
    text_data = file_content.decode('utf-8', errors='ignore')
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
                        data_dict[full_n] = {'x': [], 'y': [], 'unit': msg.get_signal_by_name(s_n).unit or ""}
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== Web 界面交互 =====================
db = load_dbc()

if not db:
    st.error(f"❌ 错误：未在服务器根目录找到 {DBC_FILENAME} 文件。")
else:
    # 1. 文件上传入口：继续保持不限制 type，解决手机变灰问题
    uploaded_file = st.file_uploader("📂 选择并上传 ASC 文件", type=None)

    if uploaded_file is not None:
        if 'data_dict' not in st.session_state:
            with st.spinner('🔍 正在深度解析电机报文数据...'):
                # 读取上传的文件内容
                file_bytes = uploaded_file.read()
                st.session_state.data_dict = process_asc(file_bytes, db)
        
        data_dict = st.session_state.data_dict

        if not data_dict:
            st.warning("⚠️ 该报文未匹配到 DBC 中的信号。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # --- 2. 核心控制面板：保留删除恢复和同步缩放功能 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                # 信号管理核心：Multiselect 支持删除、快速恢复信号
                all_sig_names = sorted(data_dict.keys())
                # 默认显示路试核心信号（如电压、电流、转速）
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["HEF", "FCCU", "Spd", "Current", "Volt"])]
                selected_sigs = st.multiselect(
                    "📌 选择/删除要显示的信号 (支持输入搜索)", 
                    options=all_sig_names,
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            
            with c2:
                # 信号同步切换功能
                sync_on = st.toggle("🔗 开启同步缩放 (所有信号联动)", value=True)
            with c3:
                # 保留当前版本显示界面交互效果的测量轴功能（Spike line）
                show_measure = st.toggle("📏 开启测量辅助线", value=True)
            
            st.divider()

            # --- 3. 全原生 JS 绘图逻辑 (核心功能：打破沙盒，原生防抖广播) ---
            if not selected_sigs:
                st.info("请在上方控制面板勾选想要查看的信号波形。")
            else:
                # 数据抽稀：手机浏览器内存极其有限，超过1万点强制抽稀以提升性能
                for name in selected_sigs:
                    x, y = data_dict[name]['x'], data_dict[name]['y']
                    if len(x) > 10000:
                        step = len(x) // 10000
                        data_dict[name]['x'], data_dict[name]['y'] = x[::step], y[::step]
                
                # 构建用于 JS 原生绘图的 JSON 数据包
                js_data_packet = {
                    "signalNames": selected_sigs,
                    "measureEnabled": show_measure,
                    "chartConfig": {
                        "modeBarButtonsToRemove": ['toImage', 'select2d', 'lasso2d'],
                        "displaylogo": False,
                        "responsive": True,
                        "scrollZoom": True
                    }
                }
                for name in selected_sigs:
                    s_label = name.split('::')[1]
                    js_data_packet[name] = {
                        "x": data_dict[name]['x'],
                        "y": data_dict[name]['y'],
                        "title": f"信号: {s_label} ({data_dict[name]['unit']})"
                    }
                
                # --- 唯一的自定义 HTML 容器 ---
                html_code = f"""
                <html>
                <head>
                    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                    <style>
                        .chart-container {{
                            margin-bottom: 20px;
                            background: white;
                            border-radius: 8px;
                            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
                            padding: 10px;
                            border: 1px solid #eee;
                        }}
                        body {{
                            background-color: #f9f9f9;
                            font-family: sans-serif;
                        }}
                    </style>
                </head>
                <body>
                    <div id="chart_stack"></div>

                    <script>
                        // 数据炸弹式注入
                        const rawData = {json.dumps(js_data_packet)};
                        const chartDivs = [];
                        let timer = null;

                        // --- 核心修复：原生 JS 防抖广播逻辑 ---
                        function syncRelayout(sourceId, update) {{
                            if (!timer) {{
                                timer = setTimeout(() => {{
                                    const xaxis_range = update;
                                    const sourceChart = document.getElementById(sourceId);
                                    
                                    // 局部异步局部更新，不卡顿
                                    chartDivs.forEach(divId => {{
                                        if (divId !== sourceId) {{
                                            Plotly.relayout(divId, xaxis_range);
                                        }
                                    });
                                    
                                    timer = null;
                                }, 50); // 50ms 延迟防抖
                            }
                        }

                        // --- 原生渲染核心 ---
                        rawData.signalNames.forEach((name, index) => {{
                            const divId = `chart_${{index}}`;
                            const container = document.createElement('div');
                            container.className = 'chart-container';
                            const chartDiv = document.createElement('div');
                            chartDiv.id = divId;
                            container.appendChild(chartDiv);
                            document.getElementById('chart_stack').appendChild(container);
                            chartDivs.push(divId);

                            const s_label = name.split('::')[1];
                            const hovermode_cfg = rawData.measureEnabled ? "x unified" : "closest";

                            const config = {{
                                data: [{{
                                    x: rawData[name].x,
                                    y: rawData[name].y,
                                    mode: 'lines',
                                    name: s_label,
                                    line: {{ width: 1.5 }}
                                }}],
                                layout: {{
                                    title: rawData[name].title,
                                    height: 350,
                                    template: "plotly_white",
                                    hovermode: hovermode_cfg,
                                    margin: {{ l: 50, r: 20, t: 50, b: 50 }},
                                    // 开启测量轴（Spike lines）
                                    xaxis: {{
                                        showgrid: True,
                                        showspikes: rawData.measureEnabled,
                                        spikemode: "across",
                                        spikedash: "dot"
                                    }},
                                    yaxis: {{ showgrid: True }}
                                }}
                            }};

                            // 原生浏览器绘图，不受 Streamlit 沙盒限制
                            Plotly.newPlot(divId, config.data, config.layout, rawData.chartConfig);
                            
                            // 监听 relayout 事件
                            if ({str(sync_on).lower()}) {{
                                document.getElementById(divId).on('plotly_relayout', (update) => {{
                                    if (update["xaxis.range[0]"]) {{
                                        syncRelayout(divId, {{
                                            "xaxis.range[0]": update["xaxis.range[0]"],
                                            "xaxis.range[1]": update["xaxis.range[1]"]
                                        }});
                                    } else if (update["xaxis.autorange"]) {{
                                        syncRelayout(divId, {{ "xaxis.autorange": true }});
                                    }
                                }});
                            }
                        });
                    </script>
                </body>
                </html>
                """
                
                # 计算 HTML 容器的总高度（每个图表约 400px）
                component_height = len(selected_sigs) * 400 + 50
                # 注入唯一的原生 HTML 容器，取代 st.plotly_chart
                components.html(html_code, height=component_height, scrolling=False)

    if st.sidebar.button("♻️ 清除缓存并重新上传"):
        # 清除所有 st.session_state 状态
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("Powered by Streamlit | JS广播防抖版")
