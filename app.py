from flask import Flask, render_template, Response
from binance import Client
import pandas as pd
import ta
from dotenv import load_dotenv
import numpy as np
import time
import os

app = Flask(__name__)

# Load environment variables from the .env file
load_dotenv()

# Get API key and secret from environment variables
API_KEY = os.getenv('API_KEY')
API_SECRET = os.getenv('SECRET_KEY')

# Initialize the Binance client
client = Client(API_KEY, API_SECRET)
# Function to retrieve symbol information and adjust quantity
def adjust_quantity(symbol, qty):
    info = client.get_symbol_info(symbol)
    lot_size = [f for f in info['filters'] if f['filterType'] == 'LOT_SIZE'][0]
    step_size = float(lot_size['stepSize'])
    min_qty = float(lot_size['minQty'])
    max_qty = float(lot_size['maxQty'])
    adjusted_qty = max(min_qty, min(qty, max_qty))
    adjusted_qty = round(adjusted_qty - (adjusted_qty % step_size), len(str(step_size).split('.')[1]))
    return adjusted_qty

# Fetch historical minute data
def getminutedata(symbol, interval, lookback):
    frame = pd.DataFrame(client.get_historical_klines(symbol, interval, lookback + ' min ago UTC'))
    frame = frame.iloc[:, :6]
    frame.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
    frame = frame.set_index('Time')
    frame.index = pd.to_datetime(frame.index, unit='ms')
    frame = frame.astype(float)
    return frame

# Apply technical indicators
def applytechnicals(df):
    df['%K'] = ta.momentum.stoch(df.High, df.Low, df.Close, window=14, smooth_window=3)
    df['%D'] = df['%K'].rolling(3).mean()
    df['rsi'] = ta.momentum.rsi(df.Close, window=14)
    df['macd'] = ta.trend.macd_diff(df.Close)
    df.dropna(inplace=True)

# Signal class
class Signals:
    def __init__(self, df, lags):
        self.df = df
        self.lags = lags

    def gettrigger(self):
        triggers = []
        for i in range(self.lags + 1):
            mask = (self.df['%K'].shift(i) < 20) & (self.df['%D'].shift(i) < 20)
            triggers.append(mask)
        return pd.DataFrame(triggers).any(axis=0)

    def decide(self):
        self.df['trigger'] = np.where(self.gettrigger(), 1, 0)
        self.df['Buy'] = np.where(
            (self.df.trigger) &
            (self.df['%K'].between(20, 80)) &
            (self.df['%D'].between(20, 80)) &
            (self.df.rsi > 50) &
            (self.df.macd > 0), 1, 0
        )

# Generator for logs
def log_stream():
    def strategy(pair, qty, open_position=False):
        df = getminutedata(pair, '1m', '100')
        applytechnicals(df)
        inst = Signals(df, 6)
        inst.decide()
        yield f'data: Current Close: {df.Close.iloc[-1]}\n\n'

        if df.Buy.iloc[-1]:
            adjusted_qty = adjust_quantity(pair, qty)
            order = client.create_order(symbol=pair, side='BUY', type='MARKET', quantity=adjusted_qty)
            yield f'data: Buy Order: {order}\n\n'
            buyprice = float(order['fills'][0]['price'])
            open_position = True

        while open_position:
            time.sleep(0.5)
            df = getminutedata(pair, '1m', '2')
            yield f'data: Current Close: {df.Close.iloc[-1]}\n\n'
            yield f'data: Target: {buyprice * 1.01}\n\n'
            yield f'data: Stop Loss: {buyprice * 0.99}\n\n'

            if df.Close.iloc[-1] <= buyprice * 0.99 or df.Close.iloc[-1] >= buyprice * 1.01:
                adjusted_qty = adjust_quantity(pair, qty * 0.998)
                order = client.create_order(symbol=pair, side='SELL', type='MARKET', quantity=adjusted_qty)
                yield f'data: Sell Order: {order}\n\n'
                break

    return strategy('ADAUSDT', 95)

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/logs')
def logs():
    return Response(log_stream(), mimetype='text/event-stream')

if __name__ == '__main__':
     app.run(host='0.0.0.0', port=8000)
