#!/usr/bin/env python3
"""
Multisig Operations Script for Junkcoin - Python Version
Handles multisig transaction creation, signing, and broadcasting
"""

import os
import sys
import json
import logging
import argparse
import subprocess
import time
import requests
from decimal import Decimal, getcontext
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import tempfile
import re

# Set high precision for cryptocurrency calculations
getcontext().prec = 28

@dataclass
class MultisigConfig:
    junkcoin_cli: str = "junkcoin-cli"
    network: str = "mainnet"
    default_fee_rate: int = 10
    min_relay_fee: int = 1000
    dust_threshold: int = 546
    confirmation_target: int = 6
    max_inputs: int = 100
    max_tx_size: int = 100000
    max_retries: int = 3
    retry_delay: int = 2
    broadcast_api: str = "https://junk-api.s3na.xyz"

class MultisigError(Exception):
    """Custom exception for multisig operations"""
    pass

class MultisigOperations:
    """Handle Junkcoin multisig operations"""
    
    def __init__(self, config: MultisigConfig = None):
        self.config = config or MultisigConfig()
        
        # Load .env file if it exists (before setting up logging)
        self._load_env_file()
        
        self.logger = self._setup_logging()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="junkcoin_multisig_"))
        
        # Load environment variables
        self._load_env_config()
    
    def _load_env_file(self):
        """Load .env file if it exists"""
        env_file = Path('.env')
        if env_file.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file)
            except ImportError:
                # Manual parsing if python-dotenv not available
                with open(env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key.strip()] = value.strip().strip('"\'')
        
    def _setup_logging(self):
        """Setup logging for multisig operations with flexible log levels"""
        logger = logging.getLogger('multisig_operations')
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Parse LOG_LEVEL from environment
        log_level_env = os.getenv('LOG_LEVEL', 'INFO')
        
        # Handle comma-separated log levels
        if ',' in log_level_env:
            # Custom log levels specified
            allowed_levels = [level.strip().upper() for level in log_level_env.split(',')]
            logger.setLevel(logging.DEBUG)  # Set to lowest level to catch everything
            
            # Create custom handler that filters by allowed levels
            console_handler = logging.StreamHandler()
            console_format = logging.Formatter('[%(asctime)s] [MULTISIG] [%(levelname)s] %(message)s')
            console_handler.setFormatter(console_format)
            
            # Add custom filter
            def log_filter(record):
                return record.levelname in allowed_levels
            
            console_handler.addFilter(log_filter)
            logger.addHandler(console_handler)
        else:
            # Single log level
            log_level = getattr(logging, log_level_env.upper(), logging.INFO)
            logger.setLevel(log_level)
            
            console_handler = logging.StreamHandler()
            console_format = logging.Formatter('[%(asctime)s] [MULTISIG] [%(levelname)s] %(message)s')
            console_handler.setFormatter(console_format)
            logger.addHandler(console_handler)
        
        return logger
    
    def _load_env_config(self):
        """Load configuration from environment variables"""
        env_mappings = {
            'JUNKCOIN_CLI': 'junkcoin_cli',
            'NETWORK': 'network',
            'DEFAULT_FEE_RATE': 'default_fee_rate',
            'MIN_RELAY_FEE': 'min_relay_fee',
            'DUST_THRESHOLD': 'dust_threshold',
            'PRIMARY_BROADCAST_API': 'broadcast_api'
        }
        
        for env_var, config_attr in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                if config_attr in ['default_fee_rate', 'min_relay_fee', 'dust_threshold']:
                    try:
                        value = int(value)
                    except ValueError:
                        continue
                setattr(self.config, config_attr, value)
    
    def validate_cli(self) -> bool:
        """Validate that junkcoin-cli is available and responsive"""
        try:
            result = subprocess.run(
                [self.config.junkcoin_cli, "getblockchaininfo"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                self.logger.error(f"Cannot connect to junkcoin daemon: {result.stderr}")
                return False
            
            # Try to parse the result as JSON
            try:
                info = json.loads(result.stdout)
                blocks = info.get('blocks', 'unknown')
                chain = info.get('chain', 'unknown')
                self.logger.info(f"Connected to {chain} network, block height: {blocks}")
                return True
            except json.JSONDecodeError:
                self.logger.warning("Connected but response is not valid JSON")
                return True  # Still functional
                
        except subprocess.TimeoutExpired:
            self.logger.error("Connection to junkcoin daemon timed out")
            return False
        except FileNotFoundError:
            self.logger.error(f"Junkcoin CLI not found: {self.config.junkcoin_cli}")
            return False
        except Exception as e:
            self.logger.error(f"Error validating CLI: {e}")
            return False
    
    def validate_address(self, address: str, address_type: str = "any") -> bool:
        """Validate Junkcoin address format"""
        if not address:
            raise MultisigError("Address cannot be empty")
        
        if address_type == "normal":
            if not address.startswith('7'):
                raise MultisigError(f"Normal Junkcoin address must start with '7': {address}")
        elif address_type == "multisig":
            if not address.startswith('3'):
                raise MultisigError(f"Multisig Junkcoin address must start with '3': {address}")
        else:  # any
            if not (address.startswith('7') or address.startswith('3')):
                raise MultisigError(f"Invalid Junkcoin address format: {address}")
        
        # Basic length and character validation - Fixed regex pattern
        address_pattern = r'^[37][123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{24,49}$'
        if not re.match(address_pattern, address):
            raise MultisigError(f"Invalid address format: {address}")
        
        return True
    
    def validate_wif(self, wif: str, key_name: str = "key") -> bool:
        """Validate Junkcoin WIF private key format"""
        if not wif:
            raise MultisigError(f"WIF key cannot be empty for {key_name}")
        
        # Junkcoin mainnet WIF keys start with 'N' and are 52 characters (not 51)
        if not wif.startswith('N'):
            raise MultisigError(f"Junkcoin WIF for {key_name} must start with 'N'")
        
        if len(wif) != 52:
            raise MultisigError(f"Junkcoin WIF for {key_name} must be 52 characters (got {len(wif)})")
        
        return True
    
    def validate_amount(self, amount: str) -> bool:
        """Validate transaction amount"""
        try:
            amount_decimal = Decimal(amount)
            if amount_decimal <= 0:
                raise MultisigError(f"Amount must be positive: {amount}")
            
            # Convert to satoshis and check dust threshold
            amount_sats = int(amount_decimal * Decimal('100000000'))
            if amount_sats < self.config.dust_threshold:
                raise MultisigError(f"Amount {amount} JKC ({amount_sats} sats) below dust threshold ({self.config.dust_threshold} sats)")
            
            return True
        except (ValueError, TypeError):
            raise MultisigError(f"Invalid amount format: {amount}")
    
    def cli_command(self, command: List[str], description: str = "CLI command") -> Dict:
        """Execute a junkcoin-cli command with retry logic"""
        full_command = [self.config.junkcoin_cli] + command
        
        for attempt in range(self.config.max_retries):
            try:
                self.logger.debug(f"Executing: {description} (attempt {attempt + 1})")
                
                result = subprocess.run(
                    full_command,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    try:
                        return json.loads(result.stdout)
                    except json.JSONDecodeError:
                        # Some commands return plain text
                        return {"result": result.stdout.strip()}
                else:
                    error_msg = result.stderr.strip()
                    if attempt < self.config.max_retries - 1:
                        self.logger.warning(f"{description} failed (attempt {attempt + 1}): {error_msg}")
                        time.sleep(self.config.retry_delay)
                        continue
                    else:
                        raise MultisigError(f"{description} failed: {error_msg}")
                        
            except subprocess.TimeoutExpired:
                if attempt < self.config.max_retries - 1:
                    self.logger.warning(f"{description} timed out (attempt {attempt + 1})")
                    time.sleep(self.config.retry_delay)
                    continue
                else:
                    raise MultisigError(f"{description} timed out after {self.config.max_retries} attempts")
            except Exception as e:
                if attempt < self.config.max_retries - 1:
                    self.logger.warning(f"{description} error (attempt {attempt + 1}): {e}")
                    time.sleep(self.config.retry_delay)
                    continue
                else:
                    raise MultisigError(f"{description} failed: {e}")
    
    def get_utxos(self, address: str, min_confirmations: int = 1) -> List[Dict]:
        """Get UTXOs for an address"""
        self.logger.info(f"Getting UTXOs for address: {address}")
        
        try:
            utxos = self.cli_command(
                ["listunspent", str(min_confirmations), "9999999", f'["{address}"]'],
                "Get UTXOs"
            )
            
            if not utxos:
                self.logger.warning(f"No UTXOs found for address: {address}")
                return []
            
            # Calculate total value
            total_value = sum(Decimal(str(utxo['amount'])) for utxo in utxos)
            
            self.logger.info(f"Found {len(utxos)} UTXOs with total value: {total_value} JKC")
            return utxos
            
        except MultisigError:
            raise
        except Exception as e:
            raise MultisigError(f"Failed to get UTXOs: {e}")
    
    def select_utxos(self, utxos: List[Dict], target_amount: Decimal, fee_reserve: int = 5000) -> Tuple[List[Dict], int]:
        """Select UTXOs for transaction with fee buffer"""
        target_sats = int(target_amount * Decimal('100000000'))
        target_with_fee = target_sats + fee_reserve
        
        # Sort UTXOs by amount (largest first for efficiency)
        sorted_utxos = sorted(utxos, key=lambda x: Decimal(str(x['amount'])), reverse=True)
        
        selected = []
        total_input_sats = 0
        
        for utxo in sorted_utxos:
            selected.append(utxo)
            utxo_sats = int(Decimal(str(utxo['amount'])) * Decimal('100000000'))
            total_input_sats += utxo_sats
            
            if total_input_sats >= target_with_fee:
                break
        
        if total_input_sats < target_sats:
            raise MultisigError(f"Insufficient funds: need {target_sats} sats, have {total_input_sats} sats")
        
        return selected, total_input_sats
    
    def estimate_fee(self, input_count: int, output_count: int) -> int:
        """Estimate transaction fee in satoshis"""
        # Rough estimation: inputs=148 bytes, outputs=34 bytes, overhead=10 bytes
        estimated_size = input_count * 148 + output_count * 34 + 10
        estimated_fee = estimated_size * self.config.default_fee_rate
        
        return max(estimated_fee, self.config.min_relay_fee)
    
    def sign_raw_transaction(self, raw_tx: str, private_keys: List[str], prevtxs: List[Dict] = None) -> Tuple[str, bool]:
        """Sign a raw transaction using available signing methods"""
        
        # Try modern signrawtransactionwithkey first
        try:
            prevtxs_param = json.dumps(prevtxs) if prevtxs else "[]"
            
            sign_result = self.cli_command([
                "signrawtransactionwithkey",
                raw_tx,
                json.dumps(private_keys),
                prevtxs_param
            ], "Sign transaction with keys")
            
            signed_tx = sign_result.get("hex", "")
            complete = sign_result.get("complete", False)
            
            if signed_tx:
                return signed_tx, complete
                
        except MultisigError as e:
            self.logger.debug(f"Modern signing failed: {e}")
        
        # Fallback to legacy signrawtransaction
        try:
            prevtxs_param = json.dumps(prevtxs) if prevtxs else "[]"
            
            sign_result = self.cli_command([
                "signrawtransaction",
                raw_tx,
                prevtxs_param,
                json.dumps(private_keys)
            ], "Sign transaction (legacy)")
            
            signed_tx = sign_result.get("hex", "")
            complete = sign_result.get("complete", False)
            
            return signed_tx, complete
            
        except MultisigError as e:
            raise MultisigError(f"Both signing methods failed: {e}")
    
    def create_and_sign_transaction(self, from_addr: str, recipients: Dict[str, str], 
                                   redeem_script: str, private_keys: List[str], 
                                   fee_rate: int = None) -> Tuple[str, bool]:
        """Create and sign a transaction"""
        
        # Validate inputs
        self.validate_address(from_addr, "multisig")
        for addr, amount in recipients.items():
            self.validate_address(addr, "any")
            self.validate_amount(amount)
        
        for i, key in enumerate(private_keys):
            self.validate_wif(key, f"key{i+1}")
        
        if not redeem_script:
            raise MultisigError("Redeem script is required")
        
        if fee_rate is None:
            fee_rate = self.config.default_fee_rate
        
        # Get UTXOs
        utxos = self.get_utxos(from_addr)
        if not utxos:
            raise MultisigError(f"No UTXOs available for address: {from_addr}")
        
        # Calculate total send amount
        total_send = sum(Decimal(amount) for amount in recipients.values())
        
        # Select UTXOs
        selected_utxos, total_input_sats = self.select_utxos(utxos, total_send)
        
        # Estimate fee
        output_count = len(recipients) + 1  # recipients + change
        estimated_fee = self.estimate_fee(len(selected_utxos), output_count)
        estimated_fee = max(estimated_fee, self.config.min_relay_fee)
        
        # Calculate change
        total_send_sats = int(total_send * Decimal('100000000'))
        change_sats = total_input_sats - total_send_sats - estimated_fee
        
        self.logger.info(f"Transaction details:")
        self.logger.info(f"  Inputs: {len(selected_utxos)} UTXOs, total: {total_input_sats / 100000000} JKC")
        self.logger.info(f"  Send total: {total_send} JKC")
        self.logger.info(f"  Estimated fee: {estimated_fee / 100000000} JKC")
        self.logger.info(f"  Change: {change_sats / 100000000} JKC")
        
        # Create inputs
        inputs = [{"txid": utxo["txid"], "vout": utxo["vout"]} for utxo in selected_utxos]
        
        # Create outputs
        outputs = {}
        for addr, amount in recipients.items():
            outputs[addr] = Decimal(amount)
        
        # Add change output if significant
        if change_sats > self.config.dust_threshold:
            change_amount = Decimal(change_sats) / Decimal('100000000')
            outputs[from_addr] = change_amount
        else:
            self.logger.info("Change amount below dust threshold, adding to fee")
        
        # Create raw transaction
        try:
            outputs_float = {addr: float(amt) for addr, amt in outputs.items()}
            raw_tx_result = self.cli_command([
                "createrawtransaction",
                json.dumps(inputs),
                json.dumps(outputs_float)
            ], "Create raw transaction")
            
            raw_tx = raw_tx_result.get("result", raw_tx_result.get("hex", ""))
            if not raw_tx:
                raise MultisigError("Failed to create raw transaction: empty result")
            
            # Prepare prevtxs for signing
            prevtxs = []
            for utxo in selected_utxos:
                prevtx = {
                    "txid": utxo["txid"],
                    "vout": utxo["vout"],
                    "scriptPubKey": utxo.get("scriptPubKey", ""),
                    "redeemScript": redeem_script,
                    "amount": utxo["amount"]
                }
                prevtxs.append(prevtx)
            
            # Sign transaction
            signed_tx, complete = self.sign_raw_transaction(raw_tx, private_keys, prevtxs)
            
            if not complete:
                raise MultisigError("Transaction signing incomplete")
            
            return signed_tx, complete
            
        except Exception as e:
            raise MultisigError(f"Failed to create/sign transaction: {e}")
    
    def broadcast_transaction_cli(self, signed_tx: str) -> str:
        """Broadcast transaction using junkcoin-cli"""
        self.logger.info("Broadcasting transaction via CLI...")
        
        try:
            result = self.cli_command([
                "sendrawtransaction",
                signed_tx
            ], "Broadcast transaction")
            
            txid = result.get("result", result.get("txid", ""))
            if not txid:
                raise MultisigError("CLI broadcast failed: empty transaction ID")
            
            self.logger.info(f"✅ Transaction broadcast successfully via CLI: {txid}")
            return txid
            
        except Exception as e:
            raise MultisigError(f"Failed to broadcast via CLI: {e}")
    
    def broadcast_transaction_api(self, signed_tx: str) -> str:
        """Broadcast transaction using API"""
        self.logger.info("Broadcasting transaction via API...")
        
        try:
            url = f"{self.config.broadcast_api.rstrip('/')}/tx"
            response = requests.post(url, data=signed_tx, headers={'Content-Type': 'text/plain'}, timeout=30)
            
            if response.status_code == 200:
                txid = response.text.strip()
                self.logger.info(f"✅ Transaction broadcast successfully via API: {txid}")
                return txid
            else:
                raise MultisigError(f"API broadcast failed: {response.status_code} - {response.text}")
                
        except requests.exceptions.RequestException as e:
            raise MultisigError(f"Failed to broadcast via API: {e}")
    
    def broadcast_transaction(self, signed_tx: str, use_api: bool = True) -> str:
        """Broadcast a signed transaction (try API first, then CLI)"""
        
        if use_api:
            try:
                return self.broadcast_transaction_api(signed_tx)
            except MultisigError as e:
                self.logger.warning(f"API broadcast failed: {e}")
                self.logger.info("Falling back to CLI broadcast...")
        
        return self.broadcast_transaction_cli(signed_tx)
    
    def auto_transaction(self, from_addr: str, to_addr: str, amount: str, 
                        redeem_script: str, private_keys: List[str], 
                        fee_rate: int = None, broadcast: bool = True, use_api: bool = True) -> str:
        """Complete transaction: create, sign, and optionally broadcast"""
        
        self.logger.info(f"=== AUTO TRANSACTION: {amount} JKC from {from_addr} to {to_addr} ===")
        
        try:
            recipients = {to_addr: amount}
            signed_tx, complete = self.create_and_sign_transaction(
                from_addr, recipients, redeem_script, private_keys, fee_rate
            )
            
            self.logger.info("✅ Transaction fully signed")
            
            # Broadcast if requested
            if broadcast:
                txid = self.broadcast_transaction(signed_tx, use_api)
                return txid
            else:
                self.logger.info("Transaction ready for manual broadcast")
                return signed_tx
                
        except MultisigError:
            raise
        except Exception as e:
            raise MultisigError(f"Auto transaction failed: {e}")
    
    def sendmany_transaction(self, from_addr: str, recipients: Dict[str, str], 
                            redeem_script: str, private_keys: List[str],
                            fee_rate: int = None, broadcast: bool = True, use_api: bool = True) -> str:
        """Send to multiple addresses in a single transaction"""
        
        self.logger.info(f"=== SENDMANY: {len(recipients)} recipients from {from_addr} ===")
        
        try:
            signed_tx, complete = self.create_and_sign_transaction(
                from_addr, recipients, redeem_script, private_keys, fee_rate
            )
            
            self.logger.info("✅ Sendmany transaction fully signed")
            
            # Broadcast if requested
            if broadcast:
                txid = self.broadcast_transaction(signed_tx, use_api)
                return txid
            else:
                self.logger.info("Sendmany transaction ready for manual broadcast")
                return signed_tx
                
        except MultisigError:
            raise
        except Exception as e:
            raise MultisigError(f"Sendmany transaction failed: {e}")
    
    def cleanup(self):
        """Clean up temporary files"""
        try:
            import shutil
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            self.logger.warning(f"Failed to clean up temp directory: {e}")

def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(description="Junkcoin Multisig Operations")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Auto command
    auto_parser = subparsers.add_parser('auto', help='Create, sign, and broadcast transaction')
    auto_parser.add_argument('from_addr', help='Source multisig address')
    auto_parser.add_argument('redeem_script', help='Redeem script (hex)')
    auto_parser.add_argument('amount', help='Amount to send (JKC)')
    auto_parser.add_argument('to_addr', help='Destination address')
    auto_parser.add_argument('key1', help='First private key (WIF)')
    auto_parser.add_argument('key2', help='Second private key (WIF)')
    auto_parser.add_argument('--fee-rate', type=int, help='Fee rate (sats/vB)')
    auto_parser.add_argument('--no-broadcast', action='store_true', help='Don\'t broadcast, return signed tx')
    auto_parser.add_argument('--use-cli', action='store_true', help='Use CLI instead of API for broadcast')
    
    # Sendmany command
    sendmany_parser = subparsers.add_parser('sendmany', help='Send to multiple addresses in one transaction')
    sendmany_parser.add_argument('from_addr', help='Source multisig address')
    sendmany_parser.add_argument('redeem_script', help='Redeem script (hex)')
    sendmany_parser.add_argument('recipients', help='Recipients JSON: {"addr1":"amount1","addr2":"amount2"}')
    sendmany_parser.add_argument('key1', help='First private key (WIF)')
    sendmany_parser.add_argument('key2', help='Second private key (WIF)')
    sendmany_parser.add_argument('--fee-rate', type=int, help='Fee rate (sats/vB)')
    sendmany_parser.add_argument('--no-broadcast', action='store_true', help='Don\'t broadcast, return signed tx')
    sendmany_parser.add_argument('--use-cli', action='store_true', help='Use CLI instead of API for broadcast')
    
    # Validate command
    subparsers.add_parser('validate', help='Validate configuration and connectivity')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Create multisig operations instance
    multisig = MultisigOperations()
    
    try:
        if args.command == 'auto':
            result = multisig.auto_transaction(
                from_addr=args.from_addr,
                to_addr=args.to_addr,
                amount=args.amount,
                redeem_script=args.redeem_script,
                private_keys=[args.key1, args.key2],
                fee_rate=args.fee_rate,
                broadcast=not args.no_broadcast,
                use_api=not args.use_cli
            )
            print(f"Success: {result}")
            
        elif args.command == 'sendmany':
            try:
                recipients = json.loads(args.recipients)
            except json.JSONDecodeError:
                print("❌ Error: Recipients must be valid JSON format")
                sys.exit(1)
                
            result = multisig.sendmany_transaction(
                from_addr=args.from_addr,
                recipients=recipients,
                redeem_script=args.redeem_script,
                private_keys=[args.key1, args.key2],
                fee_rate=args.fee_rate,
                broadcast=not args.no_broadcast,
                use_api=not args.use_cli
            )
            print(f"Success: {result}")
            
        elif args.command == 'validate':
            if multisig.validate_cli():
                print("✅ All validations passed")
            else:
                print("❌ Validation failed")
                sys.exit(1)
                
    except MultisigError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
    finally:
        multisig.cleanup()

if __name__ == "__main__":
    main()
