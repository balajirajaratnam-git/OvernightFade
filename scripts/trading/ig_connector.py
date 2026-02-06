"""
IG.com API Connector
Handles authentication, order placement, and data retrieval for IG.com trading.
Uses the trading-ig library for simplified API access.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from trading_ig import IGService
from trading_ig.config import config as ig_config
from rich.console import Console

console = Console()


class IGConnector:
    """Wrapper around trading-ig for IG.com API operations."""

    def __init__(self, credentials_path: str = "config/ig_api_credentials.json"):
        """Initialize IG connector with credentials."""
        self.credentials_path = Path(credentials_path)
        self.credentials = self._load_credentials()
        self.ig_service = None
        self.session_active = False

    def _load_credentials(self) -> Dict:
        """Load API credentials from config file."""
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {self.credentials_path}\n"
                "Please create config/ig_api_credentials.json with your API credentials."
            )

        with open(self.credentials_path, 'r') as f:
            creds = json.load(f)

        return creds

    def connect(self, use_demo: bool = None) -> bool:
        """
        Connect to IG.com API and create session.

        Args:
            use_demo: Use demo account (True) or live account (False).
                     If None, uses settings from config.

        Returns:
            True if connection successful, False otherwise.
        """
        if use_demo is None:
            use_demo = self.credentials.get('settings', {}).get('use_demo', True)

        account_type = 'demo' if use_demo else 'live'
        account_creds = self.credentials[account_type]

        console.print(f"[cyan]Connecting to IG.com {account_type.upper()} account...[/cyan]")

        try:
            # Initialize IG service
            self.ig_service = IGService(
                username=account_creds['username'],
                password=account_creds['password'],
                api_key=account_creds['api_key'],
                acc_type=account_creds['acc_type'],
                acc_number=account_creds.get('acc_number')
            )

            # Create session
            self.ig_service.create_session()
            self.session_active = True

            console.print(f"[green]Connected to IG.com {account_type.upper()} successfully[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Failed to connect to IG.com: {e}[/red]")
            self.session_active = False
            return False

    def disconnect(self):
        """Disconnect from IG.com API."""
        if self.ig_service and self.session_active:
            try:
                self.ig_service.logout()
                self.session_active = False
                console.print("[cyan]Disconnected from IG.com[/cyan]")
            except:
                pass

    def search_market(self, search_term: str) -> Optional[str]:
        """
        Search for a market (e.g., 'US 500' for SPX options).

        Args:
            search_term: Search string (e.g., 'US 500')

        Returns:
            Epic (market identifier) if found, None otherwise.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return None

        try:
            search_result = self.ig_service.search_markets(search_term)

            if search_result and len(search_result) > 0:
                # Return first match
                epic = search_result.iloc[0]['epic']
                market_name = search_result.iloc[0]['instrumentName']
                console.print(f"[green]Found market: {market_name} (Epic: {epic})[/green]")
                return epic
            else:
                console.print(f"[yellow]No markets found for: {search_term}[/yellow]")
                return None

        except Exception as e:
            console.print(f"[red]Error searching markets: {e}[/red]")
            return None

    def get_option_chain(self, underlying_epic: str, expiry_date: str) -> Optional[Dict]:
        """
        Get option chain for a given underlying and expiry.

        Args:
            underlying_epic: Epic of underlying (e.g., US 500)
            expiry_date: Expiry date in format 'YYYY-MM-DD'

        Returns:
            Dictionary with option chain data, or None if failed.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return None

        try:
            # Fetch option chain
            # Note: IG API structure may vary - adjust as needed
            options = self.ig_service.fetch_option_chain(underlying_epic, expiry_date)
            return options

        except Exception as e:
            console.print(f"[red]Error fetching option chain: {e}[/red]")
            return None

    def get_market_price(self, epic: str) -> Optional[Dict]:
        """
        Get current market price for a given epic.

        Args:
            epic: Market epic (e.g., option contract epic)

        Returns:
            Dictionary with bid, ask, mid prices, or None if failed.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return None

        try:
            market_info = self.ig_service.fetch_market_by_epic(epic)

            snapshot = market_info['snapshot']

            bid = float(snapshot['bid'])
            ask = float(snapshot['offer'])  # IG uses 'offer' for ask
            mid = (bid + ask) / 2

            return {
                'bid': bid,
                'ask': ask,
                'mid': mid,
                'timestamp': datetime.now().isoformat()
            }

        except Exception as e:
            console.print(f"[red]Error fetching market price: {e}[/red]")
            return None

    def place_market_order(
        self,
        epic: str,
        direction: str,
        size: float,
        order_type: str = 'MARKET'
    ) -> Optional[Dict]:
        """
        Place a market order.

        Args:
            epic: Market epic to trade
            direction: 'BUY' or 'SELL'
            size: Position size
            order_type: Order type (default: 'MARKET')

        Returns:
            Order confirmation dictionary, or None if failed.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return None

        try:
            console.print(f"[cyan]Placing {direction} order: {size} x {epic}[/cyan]")

            # Place order using trading-ig
            order_response = self.ig_service.create_open_position(
                epic=epic,
                direction=direction,
                currency_code='GBP',
                order_type=order_type,
                size=size,
                guaranteed_stop=False
            )

            # Extract deal reference
            deal_reference = order_response.get('dealReference')

            # Wait a moment for order to be processed
            time.sleep(2)

            # Confirm order
            confirmation = self.ig_service.fetch_deal_by_deal_reference(deal_reference)

            if confirmation['dealStatus'] == 'ACCEPTED':
                console.print(f"[green]Order ACCEPTED - Deal ID: {confirmation['dealId']}[/green]")

                return {
                    'deal_id': confirmation['dealId'],
                    'deal_reference': deal_reference,
                    'status': confirmation['dealStatus'],
                    'level': confirmation.get('level'),  # Fill price
                    'size': confirmation.get('size'),
                    'direction': direction,
                    'timestamp': datetime.now().isoformat()
                }
            else:
                console.print(f"[yellow]Order status: {confirmation['dealStatus']}[/yellow]")
                console.print(f"[yellow]Reason: {confirmation.get('reason', 'Unknown')}[/yellow]")
                return None

        except Exception as e:
            console.print(f"[red]Error placing order: {e}[/red]")
            return None

    def get_positions(self) -> Optional[Dict]:
        """
        Get all open positions.

        Returns:
            Dictionary of open positions, or None if failed.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return None

        try:
            positions = self.ig_service.fetch_open_positions()
            return positions

        except Exception as e:
            console.print(f"[red]Error fetching positions: {e}[/red]")
            return None

    def close_position(self, deal_id: str) -> bool:
        """
        Close an open position.

        Args:
            deal_id: Deal ID of position to close

        Returns:
            True if closed successfully, False otherwise.
        """
        if not self.session_active:
            console.print("[red]Not connected to IG.com[/red]")
            return False

        try:
            console.print(f"[cyan]Closing position: {deal_id}[/cyan]")

            # Close position
            close_response = self.ig_service.close_open_position(
                deal_id=deal_id,
                direction='SELL',  # Opposite of open direction
                order_type='MARKET',
                size=None  # Close entire position
            )

            deal_reference = close_response.get('dealReference')

            # Wait for confirmation
            time.sleep(2)

            confirmation = self.ig_service.fetch_deal_by_deal_reference(deal_reference)

            if confirmation['dealStatus'] == 'ACCEPTED':
                console.print(f"[green]Position closed successfully[/green]")
                return True
            else:
                console.print(f"[yellow]Close status: {confirmation['dealStatus']}[/yellow]")
                return False

        except Exception as e:
            console.print(f"[red]Error closing position: {e}[/red]")
            return False


def test_connection():
    """Test IG.com API connection."""
    console.print("\n[bold]Testing IG.com API Connection[/bold]\n")

    ig = IGConnector()

    # Connect to demo account
    if ig.connect(use_demo=True):
        console.print("[green]Connection test PASSED[/green]")

        # Test market search
        epic = ig.search_market("US 500")

        if epic:
            console.print(f"[green]Market search PASSED[/green]")

        # Disconnect
        ig.disconnect()
    else:
        console.print("[red]Connection test FAILED[/red]")


if __name__ == "__main__":
    test_connection()
