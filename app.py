# ... [代码B1(模型一)保持不变] ...
        
        # -- B2. PEG法 --
        with col2:
            with st.container(border=True):
                st.subheader("模型二：PEG估值法")
                st.caption("基于未来增长潜力 (Forward)")
                
                g_c = data['g_consensus']
                g_h = st.number_input("手动输入: 历史5年EPS增长率 %", value=10.0, step=0.5, key="g_history_input")
                
                weight = st.slider("分析师G权重 (W_c)", 0.0, 1.0, 0.7, 0.05, key="g_weight_slider")
                g_blended = (g_c * weight) + (g_h * (1-weight))
                
                st.write(f"分析师 G: **{g_c:.2f}%** | 历史 G: **{g_h:.2f}%**")
                st.write(f"混合增长率 G_Blended: **{g_blended:.2f}%**")
                
                if g_blended > 0:
                    # --- *** 这是新增加的指标框 *** ---
                    current_peg = (data['pe_ttm']) / g_blended if data['pe_ttm'] else 0
                    st.metric("当前PEG (基于混合G)", f"{current_peg:.2f}")
                    # --- *** 修改结束 *** ---
                    
                    price_low_peg = 0.8 * g_blended * data['eps_ttm'] # PEG*G*EPS
                    price_mid_peg = 1.0 * g_blended * data['eps_ttm']
                    price_high_peg = 1.5 * g_blended * data['eps_ttm']
                    
                    st.metric("估值中枢 (PEG=1.0)", f"${price_mid_peg:.2f}")
                    st.write(f"估值区间: **${price_low_peg:.2f} - ${price_high_peg:.2f}**")
                    
                    # 可靠性检查
                    if current_peg <= 0: # 变量在上面已经定义
                        st.error("可靠性: 增长率为负，PEG法失效。")
                    elif current_peg < 1.0:
                        st.success(f"可靠性: 当前PEG ({current_peg:.2f}) < 1.0")
                    else:
                        st.warning(f"可靠性: 当前PEG ({current_peg:.2f}) > 1.0")
                else:
                    # --- *** 同样在这里添加指标框 (显示为N/A) *** ---
                    st.metric("当前PEG (基于混合G)", "N/A")
                    # --- *** 修改结束 *** ---
                    st.error("可靠性: 增长率为负或零，PEG法失效。")
                    
        # ... [更新历史记录和图表的代码保持不变] ...
