import yfinance as yf
import requests
import json
import os
import sys
import csv
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- 設定 ---
PORTFOLIO_CSV_FILE = 'portfolio_tickers.csv'
OTHER_CSV_FILE = 'other_tickers.csv'

def get_webhook_url():
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("エラー: 環境変数 'SLACK_WEBHOOK_URL' が設定されていません。", file=sys.stderr)
        sys.exit(1)
    return url

def load_portfolio_targets(csv_path):
    """ポートフォリオ銘柄のCSVファイルから監視対象リストを読み込む（閾値なし）"""
    targets = []
    path = Path(csv_path)
    
    if not path.exists():
        print(f"警告: {csv_path} が見つかりません。スキップします。", file=sys.stderr)
        return targets

    try:
        with open(path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 空行などをスキップするためのガード
                if not row['ticker']:
                    continue
                targets.append({
                    'ticker': row['ticker'].strip(),
                    'company_name': row['company_name'],
                    'is_portfolio': True
                })
        return targets
    except Exception as e:
        print(f"CSV読み込みエラー: {e}", file=sys.stderr)
        return targets

def load_other_targets(csv_path):
    """その他銘柄のCSVファイルから監視対象リストを読み込む（閾値あり）"""
    targets = []
    path = Path(csv_path)
    
    if not path.exists():
        print(f"警告: {csv_path} が見つかりません。スキップします。", file=sys.stderr)
        return targets

    try:
        with open(path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 空行などをスキップするためのガード
                if not row['ticker']:
                    continue
                targets.append({
                    'ticker': row['ticker'].strip(),
                    'up': float(row['up_threshold']),
                    'down': float(row['down_threshold']),
                    'company_name': row['company_name'],
                    'is_portfolio': False
                })
        return targets
    except Exception as e:
        print(f"CSV読み込みエラー: {e}", file=sys.stderr)
        return targets

def send_slack(text, url):
    """Send text message to Slack with mrkdwn support for links"""
    payload = {
        'text': text,
        'mrkdwn': True
    }
    try:
        requests.post(
            url,
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
    except Exception as e:
        print(f"Slack送信エラー: {e}")

def generate_tradingview_url(ticker):
    """Convert yfinance ticker format to TradingView URL"""
    # 6361.T -> TSE-6361
    symbol = ticker.replace('.T', '')
    return f"https://jp.tradingview.com/symbols/TSE-{symbol}/"

def check_stock(target):
    """個別の銘柄をチェックして通知判定を行う。通知が必要な場合は辞書を返す"""
    ticker = target['ticker']
    company_name = target['company_name']
    is_portfolio = target.get('is_portfolio', False)
    
    # ポートフォリオ銘柄以外は閾値を取得
    up_thresh = target.get('up', None)
    down_thresh = target.get('down', None)

    try:
        stock = yf.Ticker(ticker)
        
        # 前日終値 (info or history)
        prev_close = stock.info.get('previousClose')
        
        # データ取得 (1日分)
        data = stock.history(period='1d', interval='1m')
        
        if data.empty:
            print(f"[{ticker}] データ取得不可 (時間外の可能性)")
            return None

        current_price = data['Close'].iloc[-1]

        # 前日終値のバックアップ取得ロジック
        if prev_close is None:
            hist_5d = stock.history(period='5d')
            if len(hist_5d) >= 2:
                prev_close = hist_5d['Close'].iloc[-2]
            else:
                print(f"[{ticker}] 前日終値不明のためスキップ")
                return None

        # 変動率計算
        change_pct = ((current_price - prev_close) / prev_close) * 100
        
        # ポートフォリオ銘柄の場合は常に表示
        if is_portfolio:
            print(f"[{ticker}] 現在: {current_price}円 / 前日比: {change_pct:+.2f}% (ポートフォリオ)")
            notification_data = {
                'ticker': ticker,
                'company_name': company_name,
                'change_pct': change_pct,
                'prev_close': prev_close,
                'current_price': current_price,
                'is_portfolio': True
            }
            print(f"[{ticker}] -> 通知対象（ポートフォリオ）")
            return notification_data
        
        # その他銘柄の場合は閾値チェック
        print(f"[{ticker}] 現在: {current_price}円 / 前日比: {change_pct:+.2f}% (閾値 +{up_thresh}% / -{down_thresh}%)")
        
        notification_data = None
        
        # 上昇チェック
        if change_pct >= up_thresh:
            notification_data = {
                'ticker': ticker,
                'company_name': company_name,
                'change_pct': change_pct,
                'prev_close': prev_close,
                'current_price': current_price,
                'status': '📈 上昇',
                'threshold': up_thresh,
                'is_portfolio': False
            }
            
        # 下落チェック (down_threshは正の値で来る想定なのでマイナスを付ける)
        elif change_pct <= -down_thresh:
            notification_data = {
                'ticker': ticker,
                'company_name': company_name,
                'change_pct': change_pct,
                'prev_close': prev_close,
                'current_price': current_price,
                'status': '📉 下落',
                'threshold': down_thresh,
                'is_portfolio': False
            }

        if notification_data:
            print(f"[{ticker}] -> 通知対象")
        
        return notification_data

    except Exception as e:
        print(f"[{ticker}] 処理エラー: {e}")
        return None

def format_notification_message(portfolio_notifications, other_notifications):
    """通知データをテキスト形式のメッセージに整形する。1銘柄あたり2行で記述"""
    if not portfolio_notifications and not other_notifications:
        return None
    
    lines = []
    
    # ポートフォリオ銘柄セクション
    if portfolio_notifications:
        lines.append("ポートフォリオ銘柄")
        for notif in portfolio_notifications:
            # 絵文字記法を決定（上昇/下落に応じて）
            emoji = ":chart_with_upwards_trend:" if notif['change_pct'] > 0 else ":chart_with_downwards_trend:"
            change_str = f"{notif['change_pct']:+.2f}%"
            
            # TradingView URLを生成してリンク形式に変換
            tradingview_url = generate_tradingview_url(notif['ticker'])
            company_ticker_text = f"{notif['company_name']} ({notif['ticker']})"
            linked_text = f"<{tradingview_url}|{company_ticker_text}>"
            
            # 1行目: 銘柄情報と変動率（閾値情報なし）
            line1 = f"{emoji} {linked_text} 前日比: {change_str}"
            
            # 2行目: 価格情報
            line2 = f"前日終値: {notif['prev_close']:.1f}円 -> 現在値: {notif['current_price']:.1f}円"
            
            lines.append(line1)
            lines.append(line2)
    
    # その他銘柄セクション
    if other_notifications:
        if portfolio_notifications:
            lines.append("===")
        lines.append("その他銘柄")
        for notif in other_notifications:
            # 絵文字記法を決定（上昇/下落に応じて）
            emoji = ":chart_with_upwards_trend:" if notif['change_pct'] > 0 else ":chart_with_downwards_trend:"
            change_str = f"{notif['change_pct']:+.2f}%"
            
            # TradingView URLを生成してリンク形式に変換
            tradingview_url = generate_tradingview_url(notif['ticker'])
            company_ticker_text = f"{notif['company_name']} ({notif['ticker']})"
            linked_text = f"<{tradingview_url}|{company_ticker_text}>"
            
            # 1行目: 銘柄情報と変動率（閾値情報あり）
            line1 = f"{emoji} {linked_text} 前日比: {change_str} (閾値: {notif['threshold']:.1f}%)"
            
            # 2行目: 価格情報
            line2 = f"前日終値: {notif['prev_close']:.1f}円 -> 現在値: {notif['current_price']:.1f}円"
            
            lines.append(line1)
            lines.append(line2)
    
    message = "\n".join(lines)
    return message

def main():
    print("--- 株価チェック開始 ---")
    webhook_url = get_webhook_url()
    
    # 2つのCSVファイルから銘柄を読み込む
    portfolio_targets = load_portfolio_targets(PORTFOLIO_CSV_FILE)
    other_targets = load_other_targets(OTHER_CSV_FILE)
    
    print(f"ポートフォリオ銘柄: {len(portfolio_targets)}銘柄")
    print(f"その他銘柄: {len(other_targets)}銘柄")
    
    # 通知データを蓄積（ポートフォリオとその他で分ける）
    portfolio_notifications = []
    other_notifications = []
    
    # ポートフォリオ銘柄をチェック（常に表示）
    for target in portfolio_targets:
        notification_data = check_stock(target)
        if notification_data:
            portfolio_notifications.append(notification_data)
    
    # その他銘柄をチェック（閾値チェック）
    for target in other_targets:
        notification_data = check_stock(target)
        if notification_data:
            other_notifications.append(notification_data)
    
    # 通知がある場合、1つのメッセージにまとめて送信
    if portfolio_notifications or other_notifications:
        message = format_notification_message(portfolio_notifications, other_notifications)
        send_slack(message, webhook_url)
        total_count = len(portfolio_notifications) + len(other_notifications)
        print(f"--- {total_count}銘柄分の通知を送信しました ---")
    else:
        print("--- 通知対象なし ---")
        
    print("--- 完了 ---")

if __name__ == "__main__":
    main()
