import os
import matplotlib
matplotlib.use('Agg') # Đảm bảo chạy không cần giao diện đồ họa
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import ta

def generate_candlestick_chart(df: pd.DataFrame, ticker: str, realtime_price: float | None = None) -> str:
    """Vẽ nến, EMA, Bollinger, RSI14, volume và giá realtime nếu có."""
    os.makedirs("temp", exist_ok=True)
    chart_path = f"temp/{ticker.upper()}_chart.png"
    
    chart_df = df.copy()

    # 1. Xử lý Index thời gian
    if 'date' in chart_df.columns:
        chart_df['date'] = pd.to_datetime(chart_df['date'])
        chart_df.set_index('date', inplace=True)
    
    # 2. Chuẩn hóa tên cột viết hoa cho mplfinance
    chart_df = chart_df.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    })

    # Đảm bảo các cột bắt buộc có kiểu dữ liệu số (numeric)
    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in cols:
        if col in chart_df.columns:
            chart_df[col] = pd.to_numeric(chart_df[col], errors='coerce')

    # Điền giá trị trống nếu có
    chart_df['Open'] = chart_df['Open'].fillna(chart_df['Close'])
    chart_df['High'] = chart_df['High'].fillna(chart_df['Close'])
    chart_df['Low'] = chart_df['Low'].fillna(chart_df['Close'])
    chart_df['Volume'] = chart_df['Volume'].fillna(0)

    # 3. Lấy 60 phiên gần nhất
    plot_df = chart_df.tail(60).copy()

    # 4. Tính toán chính xác đường EMA20 và EMA50
    plot_df['EMA20'] = ta.trend.ema_indicator(plot_df['Close'], window=20)
    plot_df['EMA50'] = ta.trend.ema_indicator(plot_df['Close'], window=50)
    plot_df['BB_HIGH'] = ta.volatility.bollinger_hband(plot_df['Close'], window=20, window_dev=2)
    plot_df['BB_LOW'] = ta.volatility.bollinger_lband(plot_df['Close'], window=20, window_dev=2)
    plot_df['RSI14'] = ta.momentum.rsi(plot_df['Close'], window=14)

    # Panel 0: giá và Bollinger. Panel 1: RSI. Panel 2: volume.
    add_plots = [
        mpf.make_addplot(plot_df['EMA20'], color='blue', width=1.2),
        mpf.make_addplot(plot_df['EMA50'], color='orange', width=1.2),
        mpf.make_addplot(plot_df['BB_HIGH'], color='gray', width=0.9, linestyle='--'),
        mpf.make_addplot(plot_df['BB_LOW'], color='gray', width=0.9, linestyle='--'),
        mpf.make_addplot(plot_df['RSI14'], panel=1, color='purple', width=1.1, ylabel='RSI14', secondary_y=False),
        mpf.make_addplot([70] * len(plot_df), panel=1, color='red', width=0.8, linestyle='--', secondary_y=False),
        mpf.make_addplot([30] * len(plot_df), panel=1, color='green', width=0.8, linestyle='--', secondary_y=False),
    ]

    if realtime_price is not None and float(realtime_price) > 0:
        realtime_line = pd.Series(float(realtime_price), index=plot_df.index)
        add_plots.append(
            mpf.make_addplot(realtime_line, color='black', width=1.0, linestyle=':')
        )

    # 5. Cấu hình màu sắc đồ thị
    market_colors = mpf.make_marketcolors(
        up='green', down='red',
        edge='inherit',
        wick='inherit',
        volume='in'
    )
    style = mpf.make_mpf_style(marketcolors=market_colors, gridstyle='--', y_on_right=False)

    # 6. Tiến hành vẽ biểu đồ
    fig, axes = mpf.plot(
        plot_df,
        type='candle',
        style=style,
        addplot=add_plots,
        volume=True,
        volume_panel=2,
        panel_ratios=(5, 2, 2),
        returnfig=True,
        savefig=dict(fname=chart_path, dpi=150, bbox_inches='tight'), # Tăng nét ảnh
        figratio=(12, 7),
        figscale=1.1
    )

    # RSI dùng thang 0-100 để vùng 30/70 có ý nghĩa trực quan.
    if len(axes) > 2:
        axes[2].set_ylim(0, 100)
        axes[2].set_ylabel('RSI14')
        axes[2].set_yticks([0, 30, 50, 70, 100])
    # mplfinance tạo axes phụ cho một số addplot; ẩn toàn bộ nhãn bên phải.
    for secondary_axis in axes[1::2]:
        secondary_axis.set_yticks([])
        secondary_axis.tick_params(left=False, right=False, labelleft=False, labelright=False)
        secondary_axis.set_ylabel('')

    # Tiêu đề và chú thích nằm ngoài vùng dữ liệu, đều căn giữa figure.
    fig.suptitle(f"Biểu đồ phân tích mã {ticker.upper()}", x=0.5, y=0.995, ha='center', fontsize=13)
    legend_text = "EMA20 xanh | EMA50 cam | Bollinger Bands (BB) nét đứt"
    if realtime_price is not None and float(realtime_price) > 0:
        legend_text += f" | Real time: {float(realtime_price):,.0f} đ"
    legend_text += "\nRSI14: 70 quá mua | 30 quá bán"
    fig.subplots_adjust(top=0.84)
    fig.text(
        0.5, 0.965,
        legend_text,
        ha='center',
        va='top',
        fontsize=8,
        bbox=dict(facecolor='white', alpha=0.75, edgecolor='none', pad=2),
    )
    fig.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    
    return chart_path