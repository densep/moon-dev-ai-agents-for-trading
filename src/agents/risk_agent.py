"""
🌙 Moon Dev's Risk Management Agent
Built with love by Moon Dev 🚀
"""

# 🛡️ Risk Override Prompt - The Secret Sauce!
RISK_OVERRIDE_PROMPT = """
You are Moon Dev's Risk Management AI 🛡️

We've hit a {limit_type} limit and need to decide whether to override it.

Analyze the provided market data for each position and decide if we should override the daily limit.
Consider for each position:
1. Recent price action and momentum (both 15m and 5m timeframes)
2. Volume patterns and trends
3. Market conditions and volatility
4. Risk/reward ratio based on current position size

For max loss overrides:
- Be EXTREMELY conservative
- Only override if strong reversal signals
- Require 90%+ confidence
- All positions must show reversal potential

For max gain overrides:
- Can be more lenient
- Look for continued momentum
- Require 60%+ confidence
- Most positions should show upward momentum

Current Positions and Data:
{position_data}

Respond with either:
OVERRIDE: <detailed reason for each position>
or
RESPECT_LIMIT: <detailed reason for each position>
"""

import sys
import os
import anthropic
import pandas as pd
import json
from termcolor import colored, cprint
from dotenv import load_dotenv
from src import config
from src import nice_funcs as n
from src.data.ohlcv_collector import collect_all_tokens  # Ensure this function exists
from datetime import datetime, timedelta
import time
from config import *
from src.agents.base_agent import BaseAgent

# Add the src directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

class RiskAgent(BaseAgent):
    def __init__(self):
        """Initialize Moon Dev's Risk Agent 🛡️"""
        super().__init__('risk')  # Initialize base agent with type
        
        api_key = os.getenv("ANTHROPIC_KEY")
        if not api_key:
            raise ValueError("🚨 ANTHROPIC_KEY not found in environment variables!")
            
        self.client = anthropic.Anthropic(api_key=api_key)
        self.override_active = False
        self.last_override_check = None
        
        # Initialize start balance using portfolio value
        self.start_balance = self.get_portfolio_value()
        print(f"🏦 Initial Portfolio Balance: ${self.start_balance:.2f}")
        
        self.current_value = self.start_balance
        cprint("🛡️ Risk Agent initialized!", "white", "on_blue")
        
    def get_portfolio_value(self):
        """Calculate total portfolio value in USD"""
        total_value = 0.0
        
        try:
            # Get USDC balance first
            usdc_value = n.get_token_balance_usd(config.USDC_ADDRESS)
            total_value += usdc_value
            
            # Get balance of each monitored token
            for token in config.MONITORED_TOKENS:
                if token != config.USDC_ADDRESS:  # Skip USDC as we already counted it
                    token_value = n.get_token_balance_usd(token)
                    total_value += token_value
                    
            return total_value
            
        except Exception as e:
            cprint(f"❌ Error calculating portfolio value: {str(e)}", "white", "on_red")
            return 0.0

    def log_daily_balance(self):
        """Log portfolio value if not logged in past check period"""
        try:
            # Create data directory if it doesn't exist
            os.makedirs('src/data', exist_ok=True)
            balance_file = 'src/data/portfolio_balance.csv'
            
            # Check if we already have a recent log
            if os.path.exists(balance_file):
                df = pd.read_csv(balance_file)
                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    last_log = df['timestamp'].max()
                    hours_since_log = (datetime.now() - last_log).total_seconds() / 3600
                    
                    if hours_since_log < config.MAX_LOSS_GAIN_CHECK_HOURS:
                        cprint(f"✨ Recent balance log found ({hours_since_log:.1f} hours ago)", "white", "on_blue")
                        return
            else:
                df = pd.DataFrame(columns=['timestamp', 'balance'])
            
            # Get current portfolio value
            current_value = self.get_portfolio_value()
            
            # Add new row
            new_row = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'balance': current_value
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            
            # Save updated log
            df.to_csv(balance_file, index=False)
            cprint(f"💾 New portfolio balance logged: ${current_value:.2f}", "white", "on_green")
            
        except Exception as e:
            cprint(f"❌ Error logging balance: {str(e)}", "white", "on_red")

    def get_position_data(self, token):
        """Get recent market data for a token"""
        try:
            # Get 8h of 15m data
            data_15m = n.get_data(token, 0.33, '15m')  # 8 hours = 0.33 days
            
            # Get 2h of 5m data
            data_5m = n.get_data(token, 0.083, '5m')   # 2 hours = 0.083 days
            
            return {
                '15m': data_15m.to_dict() if data_15m is not None else None,
                '5m': data_5m.to_dict() if data_5m is not None else None
            }
        except Exception as e:
            cprint(f"❌ Error getting data for {token}: {str(e)}", "white", "on_red")
            return None

    def should_override_limit(self, limit_type):
        """Ask AI if we should override the limit based on recent market data"""
        try:
            # Only check every 15 minutes
            if (self.last_override_check and 
                datetime.now() - self.last_override_check < timedelta(minutes=15)):
                return self.override_active
            
            # Get current positions first
            positions = n.fetch_wallet_holdings_og(address)
            
            # Filter for tokens that are both in MONITORED_TOKENS and in our positions
            # Exclude USDC and SOL
            positions = positions[
                positions['Mint Address'].isin(MONITORED_TOKENS) & 
                ~positions['Mint Address'].isin(EXCLUDED_TOKENS)
            ]
            
            if positions.empty:
                cprint("❌ No monitored positions found to analyze", "white", "on_red")
                return False
            
            # Collect data only for monitored tokens we have positions in
            position_data = {}
            for _, row in positions.iterrows():
                token = row['Mint Address']
                current_value = row['USD Value']
                
                if current_value > 0:  # Double check we have a position
                    cprint(f"📊 Getting market data for monitored position: {token}", "white", "on_blue")
                    token_data = self.get_position_data(token)
                    if token_data:
                        position_data[token] = {
                            'value_usd': current_value,
                            'data': token_data
                        }
            
            if not position_data:
                cprint("❌ Could not get market data for any monitored positions", "white", "on_red")
                return False
                
            # Format data for AI analysis
            prompt = RISK_OVERRIDE_PROMPT.format(
                limit_type=limit_type,
                position_data=json.dumps(position_data, indent=2)
            )
            
            cprint("🤖 AI Agent analyzing market data...", "white", "on_green")
            message = self.client.messages.create(
                model=config.AI_MODEL,
                max_tokens=config.AI_MAX_TOKENS,
                temperature=config.AI_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Get the response content and ensure it's a string
            response = str(message.content) if message.content else ""
            self.last_override_check = datetime.now()
            
            # Check if we should override (keep positions open)
            self.override_active = "OVERRIDE" in response.upper()
            
            # Print the AI's reasoning
            cprint("\n🧠 Risk Agent Analysis:", "white", "on_blue")
            print(response)
            
            if self.override_active:
                cprint("\n🤖 Risk Agent suggests keeping positions open", "white", "on_yellow")
            else:
                cprint("\n🛡️ Risk Agent recommends closing positions", "white", "on_red")
            
            return self.override_active
            
        except Exception as e:
            cprint(f"❌ Error in override check: {str(e)}", "white", "on_red")
            return False

    def check_pnl_limits(self):
        """Check if PnL limits have been hit"""
        try:
            self.current_value = self.get_portfolio_value()
            
            if USE_PERCENTAGE:
                # Calculate percentage change
                percent_change = ((self.current_value - self.start_balance) / self.start_balance) * 100
                
                if percent_change <= -MAX_LOSS_PERCENT:
                    cprint("\n🛑 MAXIMUM LOSS PERCENTAGE REACHED", "white", "on_red")
                    cprint(f"📉 Loss: {percent_change:.2f}% (Limit: {MAX_LOSS_PERCENT}%)", "red")
                    return True
                    
                if percent_change >= MAX_GAIN_PERCENT:
                    cprint("\n🎯 MAXIMUM GAIN PERCENTAGE REACHED", "white", "on_green")
                    cprint(f"📈 Gain: {percent_change:.2f}% (Limit: {MAX_GAIN_PERCENT}%)", "green")
                    return True
                    
            else:
                # Calculate USD change
                usd_change = self.current_value - self.start_balance
                
                if usd_change <= -MAX_LOSS_USD:
                    cprint("\n🛑 MAXIMUM LOSS USD REACHED", "white", "on_red")
                    cprint(f"📉 Loss: ${abs(usd_change):.2f} (Limit: ${MAX_LOSS_USD:.2f})", "red")
                    return True
                    
                if usd_change >= MAX_GAIN_USD:
                    cprint("\n🎯 MAXIMUM GAIN USD REACHED", "white", "on_green")
                    cprint(f"📈 Gain: ${usd_change:.2f} (Limit: ${MAX_GAIN_USD:.2f})", "green")
                    return True
            
            return False
            
        except Exception as e:
            cprint(f"❌ Error checking PnL limits: {e}", "red")
            return False

    def close_all_positions(self):
        """Close all monitored positions except USDC and SOL"""
        try:
            cprint("\n🔄 Closing monitored positions...", "white", "on_cyan")
            
            # Get all positions
            positions = n.fetch_wallet_holdings_og(address)
            
            # Debug print to see what we're working with
            cprint("\n📊 Current positions:", "cyan")
            print(positions)
            cprint("\n🎯 Monitored tokens:", "cyan")
            print(MONITORED_TOKENS)
            
            # Filter for tokens that are both in MONITORED_TOKENS and not in EXCLUDED_TOKENS
            positions = positions[
                positions['Mint Address'].isin(MONITORED_TOKENS) & 
                ~positions['Mint Address'].isin(EXCLUDED_TOKENS)
            ]
            
            if positions.empty:
                cprint("📝 No monitored positions to close", "white", "on_blue")
                return
                
            # Close each monitored position
            for _, row in positions.iterrows():
                token = row['Mint Address']
                value = row['USD Value']
                
                cprint(f"\n💰 Closing position: {token} (${value:.2f})", "white", "on_cyan")
                try:
                    n.chunk_kill(token, max_usd_order_size, slippage)
                    cprint(f"✅ Successfully closed position for {token}", "white", "on_green")
                except Exception as e:
                    cprint(f"❌ Error closing position for {token}: {str(e)}", "white", "on_red")
                    
            cprint("\n✨ All monitored positions closed", "white", "on_green")
            
        except Exception as e:
            cprint(f"❌ Error in close_all_positions: {str(e)}", "white", "on_red")

    def check_risk_limits(self):
        """Check if any risk limits have been breached"""
        try:
            # Get current PnL
            current_pnl = self.get_current_pnl()
            current_balance = self.get_portfolio_value()
            
            print(f"\n💰 Current PnL: ${current_pnl:.2f}")
            print(f"💼 Current Balance: ${current_balance:.2f}")
            print(f"📉 Minimum Balance Limit: ${MINIMUM_BALANCE_USD:.2f}")
            
            # Check minimum balance limit
            if current_balance < MINIMUM_BALANCE_USD:
                print(f"⚠️ ALERT: Current balance ${current_balance:.2f} is below minimum ${MINIMUM_BALANCE_USD:.2f}")
                self.handle_limit_breach("MINIMUM_BALANCE", current_balance)
                return True
            
            # Check PnL limits
            if USE_PERCENTAGE:
                if abs(current_pnl) >= MAX_LOSS_PERCENT:
                    print(f"⚠️ PnL limit reached: {current_pnl}%")
                    self.handle_limit_breach("PNL_PERCENT", current_pnl)
                    return True
            else:
                if abs(current_pnl) >= MAX_LOSS_USD:
                    print(f"⚠️ PnL limit reached: ${current_pnl:.2f}")
                    self.handle_limit_breach("PNL_USD", current_pnl)
                    return True
                    
            print("✅ All risk limits OK")
            return False
            
        except Exception as e:
            print(f"❌ Error checking risk limits: {str(e)}")
            return False
            
    def handle_limit_breach(self, breach_type, current_value):
        """Handle breached risk limits with AI consultation if enabled"""
        try:
            # If AI confirmation is disabled, close positions immediately
            if not USE_AI_CONFIRMATION:
                print(f"\n🚨 {breach_type} limit breached! Closing all positions immediately...")
                print(f"💡 (AI confirmation disabled in config)")
                self.close_all_positions()
                return
                
            # Get all current positions using fetch_wallet_holdings_og
            positions_df = n.fetch_wallet_holdings_og(address)
            
            # Prepare breach context
            if breach_type == "MINIMUM_BALANCE":
                context = f"Current balance (${current_value:.2f}) has fallen below minimum balance limit (${MINIMUM_BALANCE_USD:.2f})"
            elif breach_type == "PNL_USD":
                context = f"Current PnL (${current_value:.2f}) has exceeded USD limit (${MAX_LOSS_USD:.2f})"
            else:
                context = f"Current PnL ({current_value}%) has exceeded percentage limit ({MAX_LOSS_PERCENT}%)"
            
            # Format positions for AI
            positions_str = "\nCurrent Positions:\n"
            for _, row in positions_df.iterrows():
                if row['USD Value'] > 0:
                    positions_str += f"- {row['Mint Address']}: {row['Amount']} (${row['USD Value']:.2f})\n"
                    
            # Get AI recommendation
            prompt = f"""
🚨 RISK LIMIT BREACH ALERT 🚨

{context}

{positions_str}

Should we close all positions immediately? Consider:
1. Market conditions
2. Position sizes
3. Recent price action
4. Risk of further losses

Respond with:
CLOSE_ALL or HOLD_POSITIONS
Then explain your reasoning.
"""
            # Get AI decision
            message = self.client.messages.create(
                model=AI_MODEL,
                max_tokens=AI_MAX_TOKENS,
                temperature=AI_TEMPERATURE,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            
            response = message.content
            if isinstance(response, list):
                response = '\n'.join([item.text if hasattr(item, 'text') else str(item) for item in response])
            
            print("\n🤖 AI Risk Assessment:")
            print("=" * 50)
            print(response)
            print("=" * 50)
            
            # Parse decision
            decision = response.split('\n')[0].strip()
            
            if decision == "CLOSE_ALL":
                print("🚨 AI recommends closing all positions!")
                self.close_all_positions()
            else:
                print("✋ AI recommends holding positions despite breach")
                
        except Exception as e:
            print(f"❌ Error handling limit breach: {str(e)}")
            # Default to closing positions on error
            print("⚠️ Error in AI consultation - defaulting to close all positions")
            self.close_all_positions()

    def get_current_pnl(self):
        """Calculate current PnL based on start balance"""
        try:
            current_value = self.get_portfolio_value()
            print(f"\n💰 Start Balance: ${self.start_balance:.2f}")
            print(f"📊 Current Value: ${current_value:.2f}")
            
            pnl = current_value - self.start_balance
            print(f"📈 Current PnL: ${pnl:.2f}")
            return pnl
            
        except Exception as e:
            print(f"❌ Error calculating PnL: {str(e)}")
            return 0.0

    def run(self):
        """Run the risk agent (implements BaseAgent interface)"""
        try:
            # Get current PnL
            current_pnl = self.get_current_pnl()
            current_balance = self.get_portfolio_value()
            
            print(f"\n💰 Current PnL: ${current_pnl:.2f}")
            print(f"💼 Current Balance: ${current_balance:.2f}")
            print(f"📉 Minimum Balance Limit: ${MINIMUM_BALANCE_USD:.2f}")
            
            # Check minimum balance limit
            if current_balance < MINIMUM_BALANCE_USD:
                print(f"⚠️ ALERT: Current balance ${current_balance:.2f} is below minimum ${MINIMUM_BALANCE_USD:.2f}")
                self.handle_limit_breach("MINIMUM_BALANCE", current_balance)
                return True
            
            # Check PnL limits
            if USE_PERCENTAGE:
                if abs(current_pnl) >= MAX_LOSS_PERCENT:
                    print(f"⚠️ PnL limit reached: {current_pnl}%")
                    self.handle_limit_breach("PNL_PERCENT", current_pnl)
                    return True
            else:
                if abs(current_pnl) >= MAX_LOSS_USD:
                    print(f"⚠️ PnL limit reached: ${current_pnl:.2f}")
                    self.handle_limit_breach("PNL_USD", current_pnl)
                    return True
                    
            print("✅ All risk limits OK")
            return False
            
        except Exception as e:
            print(f"❌ Error checking risk limits: {str(e)}")
            return False

def main():
    """Main function to run the risk agent"""
    cprint("🛡🛡🛡️ Risk Agent Starting...", "white", "on_blue")
    
    agent = RiskAgent()
    
    while True:
        try:
            # Always try to log balance (function will check if 12 hours have passed)
            agent.log_daily_balance()
            
            # Always check PnL limits
            agent.check_pnl_limits()
            
            # Sleep for 5 minutes before next check
            time.sleep(300)
                
        except KeyboardInterrupt:
            print("\n👋 Risk Agent shutting down gracefully...")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            print("🔧 Moon Dev suggests checking the logs and trying again!")
            time.sleep(300)  # Still sleep on error

if __name__ == "__main__":
    main()

