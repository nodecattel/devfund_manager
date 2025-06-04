#!/usr/bin/env python3

import os
import sys
import json
import logging
import argparse
import signal
import subprocess
from decimal import Decimal, getcontext
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple
from pathlib import Path
import re

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests")
    sys.exit(1)

getcontext().prec = 28

@dataclass
class Config:
    devfund_address: str = "34P2otqp4hUL4kRoVH74KpyrBdkrqZM18n"
    liquidity_address: str = "7V768f6woVJ8QKRpfGMMA5pm24ysE6Dh3u"
    dev_address: str = "7YrjoRZzfjZ15Y6bqrZMogCU1E8j65mUmj"
    marketing_address: str = "7baGFmUcE2xQaqCMrHLVcLV6YkputE3PUk"
    signer1_wif: str = ""
    signer2_wif: str = ""
    threshold_utxo: int = 1
    threshold_balance_sats: int = 1000000000
    minimum_balance_sats: int = 1000000000  # 10 JKC minimum balance
    liquidity_percent: int = 50
    dev_percent: int = 25
    marketing_percent: int = 25
    primary_api: str = "https://junk-api.s3na.xyz"
    fallback_api: str = "https://junk-api.s3na.xyz"
    log_level: str = "INFO"
    api_timeout: int = 30
    max_retries: int = 3
    min_distribution_amount: int = 10000000
    fee_rate: int = 100
    network: str = "mainnet"
    multisig_m: int = 2
    multisig_n: int = 3
    redeem_script: str = ""
    use_sendmany: bool = True  # Use single transaction for all distributions
    
    script_dir: Path = field(default_factory=lambda: Path.cwd())
    env_file: Path = field(init=False)
    log_file: Path = field(init=False)
    multisig_script: Path = field(init=False)
    
    def __post_init__(self):
        self.env_file = self.script_dir / ".env"
        self.log_file = self.script_dir / "devfund_manager.log"
        self.multisig_script = self.script_dir / "multisig_op.py"

class ValidationError(Exception):
    pass

class APIError(Exception):
    pass

class DevFundManager:
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = self._setup_logging()
        self.session = self._setup_session()
        signal.signal(signal.SIGINT, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        self.logger.info(f"Shutting down gracefully...")
        sys.exit(0)
        
    def _setup_logging(self):
        logger = logging.getLogger('devfund_manager')
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Parse LOG_LEVEL from environment or config
        log_level_env = os.getenv('LOG_LEVEL', self.config.log_level)
        
        # Handle comma-separated log levels
        if ',' in log_level_env:
            # Custom log levels specified
            allowed_levels = [level.strip().upper() for level in log_level_env.split(',')]
            logger.setLevel(logging.DEBUG)  # Set to lowest level to catch everything
            
            # Create custom handler that filters by allowed levels
            console_handler = logging.StreamHandler()
            console_format = logging.Formatter('[%(levelname)s] %(message)s')
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
            console_format = logging.Formatter('[%(levelname)s] %(message)s')
            console_handler.setFormatter(console_format)
            logger.addHandler(console_handler)
        
        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(self.config.log_file, maxBytes=10*1024*1024, backupCount=5)
            file_format = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
            file_handler.setFormatter(file_format)
            logger.addHandler(file_handler)
            os.chmod(self.config.log_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not setup file logging: {e}")
        
        return logger
    
    def _setup_session(self):
        session = requests.Session()
        retry_strategy = Retry(total=self.config.max_retries, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session
    
    def load_environment(self):
        if self.config.env_file.exists():
            self.logger.info(f"Loading environment from {self.config.env_file}")
            
            try:
                from dotenv import load_dotenv
                load_dotenv(self.config.env_file)
            except ImportError:
                with open(self.config.env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key.strip()] = value.strip().strip('"\'')
            
            self._update_config_from_env()
    
    def _update_config_from_env(self):
        env_mappings = {
            'DEVFUND_ADDRESS': 'devfund_address',
            'LIQUIDITY_ADDRESS': 'liquidity_address',
            'DEV_ADDRESS': 'dev_address',
            'MARKETING_ADDRESS': 'marketing_address',
            'SIGNER1_WIF': 'signer1_wif',
            'SIGNER2_WIF': 'signer2_wif',
            'THRESHOLD_UTXO': 'threshold_utxo',
            'THRESHOLD_BALANCE_SATS': 'threshold_balance_sats',
            'MINIMUM_BALANCE_SATS': 'minimum_balance_sats',
            'LIQUIDITY_PERCENT': 'liquidity_percent',
            'DEV_PERCENT': 'dev_percent',
            'MARKETING_PERCENT': 'marketing_percent',
            'PRIMARY_API': 'primary_api',
            'FALLBACK_API': 'fallback_api',
            'REDEEM_SCRIPT': 'redeem_script',
            'USE_SENDMANY': 'use_sendmany',
            'FEE_RATE': 'fee_rate',
            'MIN_DISTRIBUTION_AMOUNT': 'min_distribution_amount',
            'API_TIMEOUT': 'api_timeout',
            'MAX_RETRIES': 'max_retries'
        }
        
        for env_var, config_attr in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                if config_attr.endswith(('_utxo', '_sats', '_percent', '_rate', '_amount', '_timeout', '_retries')):
                    try:
                        value = int(value)
                    except ValueError:
                        continue
                elif config_attr == 'use_sendmany':
                    value = value.lower() in ('true', '1', 'yes', 'on')
                setattr(self.config, config_attr, value)
    
    def validate_configuration(self):
        self.logger.info("Validating configuration...")
        errors = []
        
        if not self.config.multisig_script.exists():
            errors.append(f"Multisig script not found: {self.config.multisig_script}")
        
        if not self.config.redeem_script:
            errors.append("REDEEM_SCRIPT is required for execution")
        
        if not self.config.signer1_wif or not self.config.signer2_wif:
            errors.append("SIGNER1_WIF and SIGNER2_WIF are required for execution")
        
        total_percent = self.config.liquidity_percent + self.config.dev_percent + self.config.marketing_percent
        if total_percent != 100:
            errors.append(f"Percentages don't add up to 100% (current: {total_percent}%)")
        
        # Validate minimum balance is reasonable
        if self.config.minimum_balance_sats < 0:
            errors.append("Minimum balance cannot be negative")
        
        if errors:
            for error in errors:
                self.logger.error(f"  - {error}")
            raise ValidationError("Configuration validation failed")
        
        self.logger.info("Configuration validation passed")
    
    def api_call(self, endpoint: str, description: str = "API call"):
        url = f"{self.config.primary_api.rstrip('/')}/{endpoint.lstrip('/')}"
        self.logger.debug(f"API call: {url}")
        
        try:
            response = self.session.get(url, timeout=self.config.api_timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if self.config.fallback_api:
                fallback_url = f"{self.config.fallback_api.rstrip('/')}/{endpoint.lstrip('/')}"
                self.logger.warning(f"Primary failed, trying fallback: {fallback_url}")
                try:
                    response = self.session.get(fallback_url, timeout=self.config.api_timeout)
                    response.raise_for_status()
                    return response.json()
                except Exception:
                    pass
            raise APIError(f"{description} failed: {e}")
    
    @staticmethod
    def sats_to_jkc(sats: int):
        return Decimal(sats) / Decimal('100000000')
    
    def estimate_transaction_size(self, num_inputs=1, num_outputs=3):
        """
        Estimate transaction size in virtual bytes (vB) for fee calculation
        
        Typical multisig transaction structure:
        - Base transaction: ~10 vB
        - Each P2SH input: ~295 vB (2-of-3 multisig signature)
        - Each P2PKH output: ~34 vB
        - Each P2SH output: ~32 vB
        """
        base_size = 10
        input_size = num_inputs * 295  # P2SH multisig inputs are large
        output_size = num_outputs * 34  # Assume P2PKH outputs
        
        estimated_size = base_size + input_size + output_size
        
        # Add 10% buffer for safety
        return int(estimated_size * 1.1)
    
    def calculate_distribution(self, total_balance: int):
        """Calculate distribution amounts while respecting minimum balance"""
        
        # FIXED: Proper fee calculation based on actual transaction size
        estimated_tx_size = self.estimate_transaction_size(num_inputs=1, num_outputs=3)
        fee_sats = self.config.fee_rate * estimated_tx_size
        
        # Add 50% buffer for network congestion
        fee_reserve = int(fee_sats * 1.5)
        
        total_reserve = self.config.minimum_balance_sats + fee_reserve
        
        if total_balance <= total_reserve:
            raise ValueError(f"Insufficient balance for distribution. Need at least {self.sats_to_jkc(total_reserve)} JKC")
        
        distributable = total_balance - total_reserve
        distributable_decimal = Decimal(distributable)
        
        self.logger.info(f"Balance calculation:")
        self.logger.info(f"  Total balance: {self.sats_to_jkc(total_balance)} JKC")
        self.logger.info(f"  Minimum reserve: {self.sats_to_jkc(self.config.minimum_balance_sats)} JKC")
        self.logger.info(f"  Estimated TX size: {estimated_tx_size} vB")
        self.logger.info(f"  Fee rate: {self.config.fee_rate} sats/vB")
        self.logger.info(f"  Calculated fee: {self.sats_to_jkc(fee_sats)} JKC ({fee_sats} sats)")
        self.logger.info(f"  Fee reserve (with 50% buffer): {self.sats_to_jkc(fee_reserve)} JKC")
        self.logger.info(f"  Distributable: {self.sats_to_jkc(distributable)} JKC")
        
        liquidity_amount = int(distributable_decimal * Decimal(self.config.liquidity_percent) / Decimal('100'))
        dev_amount = int(distributable_decimal * Decimal(self.config.dev_percent) / Decimal('100'))
        marketing_amount = int(distributable_decimal * Decimal(self.config.marketing_percent) / Decimal('100'))
        
        # Handle any remainder (usually small rounding difference)
        remainder = distributable - (liquidity_amount + dev_amount + marketing_amount)
        dev_amount += remainder
        
        return liquidity_amount, dev_amount, marketing_amount
    
    def get_address_info(self):
        utxos_data = self.api_call(f"address/{self.config.devfund_address}/utxo?limit=10000", "UTXO fetch")
        utxo_count = utxos_data.get('total', 0)
        
        balance_data = self.api_call(f"address/{self.config.devfund_address}", "Balance fetch")
        chain_stats = balance_data.get('chain_stats', {})
        funded = chain_stats.get('funded_txo_sum', 0)
        spent = chain_stats.get('spent_txo_sum', 0)
        balance = funded - spent
        
        return utxo_count, balance, funded, spent
    
    def should_distribute(self):
        try:
            utxo_count, balance, _, _ = self.get_address_info()
            utxo_met = utxo_count >= self.config.threshold_utxo
            balance_met = balance >= self.config.threshold_balance_sats
            
            # Also check if we have enough above minimum balance
            distributable_check = balance > (self.config.minimum_balance_sats + self.config.min_distribution_amount)
            
            return utxo_met and balance_met and distributable_check, utxo_count, balance
        except APIError:
            return False, 0, 0
    
    def execute_multisig_sendmany(self, recipients: Dict[str, float]):
        """Execute sendmany transaction for all distributions at once"""
        self.logger.info("Executing sendmany distribution to all recipients")
        
        recipients_json = json.dumps(recipients)
        
        # Calculate proper fee for the transaction
        estimated_tx_size = self.estimate_transaction_size(num_inputs=1, num_outputs=len(recipients))
        calculated_fee_sats = self.config.fee_rate * estimated_tx_size
        
        # Add buffer for network congestion
        final_fee_rate = int(self.config.fee_rate * 1.5)  # 50% higher for reliability
        
        self.logger.info(f"Transaction fee calculation:")
        self.logger.info(f"  Estimated size: {estimated_tx_size} vB")
        self.logger.info(f"  Base fee rate: {self.config.fee_rate} sats/vB")
        self.logger.info(f"  Final fee rate (with buffer): {final_fee_rate} sats/vB")
        self.logger.info(f"  Expected total fee: {self.sats_to_jkc(final_fee_rate * estimated_tx_size)} JKC")
        
        cmd = [
            "python3",
            str(self.config.multisig_script.absolute()),
            "sendmany",
            self.config.devfund_address,
            self.config.redeem_script,
            recipients_json,
            self.config.signer1_wif,
            self.config.signer2_wif,
            "--fee-rate", str(final_fee_rate)  # Use buffered fee rate
        ]
        
        try:
            self.logger.debug(f"Working directory: {self.config.script_dir}")
            self.logger.debug(f"Running sendmany command with {len(recipients)} recipients")
            
            # Set environment and run from script directory
            env = os.environ.copy()
            env['FEE_RATE'] = str(final_fee_rate)  # Pass fee rate via environment too
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=300,
                cwd=str(self.config.script_dir),
                env=env
            )
            
            if result.returncode == 0:
                self.logger.info("✅ Sendmany distribution successful")
                # Extract transaction ID from output
                output_lines = result.stdout.strip().split('\n')
                for line in output_lines:
                    if 'Success:' in line:
                        txid = line.split('Success:')[-1].strip()
                        self.logger.info(f"💎 Transaction ID: {txid}")
                        return txid
                
                # Look for transaction ID in any line
                import re
                for line in output_lines:
                    txid_match = re.search(r'\b([a-fA-F0-9]{64})\b', line)
                    if txid_match:
                        txid = txid_match.group(1)
                        self.logger.info(f"💎 Transaction ID: {txid}")
                        return txid
                
                return "SUCCESS"
            else:
                self.logger.error("❌ Sendmany distribution failed")
                self.logger.error(f"Return code: {result.returncode}")
                
                # Enhanced error analysis
                error_output = result.stderr + result.stdout
                if 'insufficient priority' in error_output.lower():
                    self.logger.error(f"💡 Fee too low! Current rate: {final_fee_rate} sats/vB")
                    self.logger.error(f"💡 Try increasing FEE_RATE in .env to 1000-3000 sats/vB")
                elif 'insufficient funds' in error_output.lower():
                    self.logger.error(f"💡 Not enough funds for transaction + fees")
                
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                if result.stdout:
                    self.logger.error(f"STDOUT: {result.stdout}")
                return None
                
        except subprocess.TimeoutExpired:
            self.logger.error("❌ Sendmany distribution timed out")
            return None
        except Exception as e:
            self.logger.error(f"❌ Sendmany distribution error: {e}")
            return None
    
    def execute_multisig_transaction(self, from_addr: str, to_addr: str, amount_jkc: float, description: str):
        """Execute a multisig transaction using the Python multisig_op.py"""
        self.logger.info(f"Executing {description}: {amount_jkc} JKC → {to_addr}")
        
        cmd = [
            "python3",
            str(self.config.multisig_script.absolute()),
            "auto",
            from_addr,
            self.config.redeem_script,
            str(amount_jkc),
            to_addr,
            self.config.signer1_wif,
            self.config.signer2_wif,
            "--fee-rate", str(self.config.fee_rate)
        ]
        
        try:
            self.logger.debug(f"Working directory: {self.config.script_dir}")
            self.logger.debug(f"Running command: python3 multisig_op.py auto [REDACTED_ARGS]")
            
            # Set environment and run from script directory
            env = os.environ.copy()
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=300,
                cwd=str(self.config.script_dir),
                env=env
            )
            
            if result.returncode == 0:
                self.logger.info(f"✅ {description} transaction successful")
                # Extract transaction ID from output
                output_lines = result.stdout.strip().split('\n')
                for line in output_lines:
                    if 'Success:' in line:
                        txid = line.split('Success:')[-1].strip()
                        self.logger.info(f"Transaction ID: {txid}")
                return True
            else:
                self.logger.error(f"❌ {description} transaction failed")
                self.logger.error(f"Error: {result.stderr}")
                if result.stdout:
                    self.logger.debug(f"Output: {result.stdout}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"❌ {description} transaction timed out")
            return False
        except Exception as e:
            self.logger.error(f"❌ {description} transaction error: {e}")
            return False
    
    def execute_distribution(self):
        """Execute the full distribution process"""
        self.logger.info("=== EXECUTING DISTRIBUTION ===")
        
        should_dist, utxo_count, balance = self.should_distribute()
        if not should_dist:
            self.logger.error("Thresholds no longer met, aborting execution")
            return False
        
        try:
            liquidity_amount, dev_amount, marketing_amount = self.calculate_distribution(balance)
            
            liquidity_jkc = float(self.sats_to_jkc(liquidity_amount))
            dev_jkc = float(self.sats_to_jkc(dev_amount))
            marketing_jkc = float(self.sats_to_jkc(marketing_amount))
            
            self.logger.info("Distribution plan:")
            self.logger.info(f"  Liquidity: {liquidity_jkc} JKC → {self.config.liquidity_address}")
            self.logger.info(f"  Dev: {dev_jkc} JKC → {self.config.dev_address}")
            self.logger.info(f"  Marketing: {marketing_jkc} JKC → {self.config.marketing_address}")
            
            if self.config.use_sendmany:
                # Use sendmany for single transaction
                recipients = {
                    self.config.liquidity_address: liquidity_jkc,
                    self.config.dev_address: dev_jkc,
                    self.config.marketing_address: marketing_jkc
                }
                
                txid = self.execute_multisig_sendmany(recipients)
                if txid:
                    self.logger.info("🎉 Sendmany distribution completed successfully!")
                    return True
                else:
                    self.logger.error("❌ Sendmany distribution failed")
                    return False
            else:
                # Use individual transactions (fallback)
                success_count = 0
                transactions = [
                    (self.config.liquidity_address, liquidity_jkc, "Liquidity distribution"),
                    (self.config.dev_address, dev_jkc, "Dev distribution"),
                    (self.config.marketing_address, marketing_jkc, "Marketing distribution")
                ]
                
                for to_addr, amount, desc in transactions:
                    if self.execute_multisig_transaction(self.config.devfund_address, to_addr, amount, desc):
                        success_count += 1
                        # Wait a bit between transactions to avoid issues
                        import time
                        time.sleep(5)
                
                if success_count == 3:
                    self.logger.info("🎉 All distributions completed successfully!")
                    return True
                else:
                    self.logger.warning(f"⚠️  Only {success_count}/3 distributions succeeded")
                    return False
                
        except ValueError as e:
            self.logger.error(f"Distribution calculation failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during execution: {e}")
            return False
    
    def dry_run(self):
        self.logger.info("=== DRY RUN MODE ===")
        
        try:
            utxo_count, balance, funded, spent = self.get_address_info()
        except APIError as e:
            self.logger.error(f"Failed to get address information: {e}")
            return
        
        self.logger.info("Current state:")
        self.logger.info(f"  Address: {self.config.devfund_address}")
        self.logger.info(f"  UTXOs: {utxo_count} (threshold: {self.config.threshold_utxo})")
        self.logger.info(f"  Balance: {self.sats_to_jkc(balance)} JKC (threshold: {self.sats_to_jkc(self.config.threshold_balance_sats)} JKC)")
        self.logger.info(f"  Minimum balance: {self.sats_to_jkc(self.config.minimum_balance_sats)} JKC (will be preserved)")
        
        utxo_met = utxo_count >= self.config.threshold_utxo
        balance_met = balance >= self.config.threshold_balance_sats
        distributable_check = balance > (self.config.minimum_balance_sats + self.config.min_distribution_amount)
        
        if utxo_met and balance_met and distributable_check:
            self.logger.info("✓ Thresholds met - distribution would occur")
            
            try:
                liquidity_amount, dev_amount, marketing_amount = self.calculate_distribution(balance)
                
                self.logger.info("")
                self.logger.info("Distribution that would occur:")
                self.logger.info(f"  Liquidity ({self.config.liquidity_percent}%): {self.sats_to_jkc(liquidity_amount)} JKC → {self.config.liquidity_address}")
                self.logger.info(f"  Dev ({self.config.dev_percent}%): {self.sats_to_jkc(dev_amount)} JKC → {self.config.dev_address}")
                self.logger.info(f"  Marketing ({self.config.marketing_percent}%): {self.sats_to_jkc(marketing_amount)} JKC → {self.config.marketing_address}")
                
                if self.config.use_sendmany:
                    self.logger.info("  Method: Single sendmany transaction (efficient)")
                else:
                    self.logger.info("  Method: 3 separate transactions")
                
            except ValueError as e:
                self.logger.error(f"Distribution calculation failed: {e}")
        else:
            self.logger.info("✗ Thresholds not met - no action would be taken")
            if not utxo_met:
                self.logger.info(f"  ✗ UTXO threshold: need {self.config.threshold_utxo}, have {utxo_count}")
            if not balance_met:
                needed = self.config.threshold_balance_sats - balance
                self.logger.info(f"  ✗ Balance threshold: need {self.sats_to_jkc(needed)} more JKC")
            if not distributable_check:
                min_needed = self.config.minimum_balance_sats + self.config.min_distribution_amount
                self.logger.info(f"  ✗ Insufficient distributable balance: need {self.sats_to_jkc(min_needed)} JKC total")
        
        self.logger.info("=== DRY RUN COMPLETE ===")
    
    def show_config(self):
        self.logger.info("=== CURRENT CONFIGURATION ===")
        self.logger.info("Addresses:")
        self.logger.info(f"  DevFund: {self.config.devfund_address}")
        self.logger.info(f"  Liquidity: {self.config.liquidity_address}")
        self.logger.info(f"  Dev: {self.config.dev_address}")
        self.logger.info(f"  Marketing: {self.config.marketing_address}")
        self.logger.info("")
        self.logger.info("Thresholds:")
        self.logger.info(f"  UTXOs: {self.config.threshold_utxo}")
        self.logger.info(f"  Balance: {self.sats_to_jkc(self.config.threshold_balance_sats)} JKC")
        self.logger.info(f"  Minimum balance (preserved): {self.sats_to_jkc(self.config.minimum_balance_sats)} JKC")
        self.logger.info(f"  Minimum distribution: {self.sats_to_jkc(self.config.min_distribution_amount)} JKC")
        self.logger.info("")
        self.logger.info("Distribution:")
        self.logger.info(f"  Liquidity: {self.config.liquidity_percent}%")
        self.logger.info(f"  Dev: {self.config.dev_percent}%")
        self.logger.info(f"  Marketing: {self.config.marketing_percent}%")
        self.logger.info(f"  Method: {'Sendmany (single tx)' if self.config.use_sendmany else 'Individual transactions'}")
        self.logger.info("")
        self.logger.info("Transaction Settings:")
        self.logger.info(f"  Fee Rate: {self.config.fee_rate} sats/vB")
        self.logger.info(f"  Network: {self.config.network}")
        self.logger.info(f"  Multisig: {self.config.multisig_m}-of-{self.config.multisig_n}")
        self.logger.info("")
        self.logger.info("API Settings:")
        self.logger.info(f"  Primary API: {self.config.primary_api}")
        self.logger.info(f"  Fallback API: {self.config.fallback_api}")
        self.logger.info(f"  Timeout: {self.config.api_timeout}s")
        self.logger.info(f"  Max Retries: {self.config.max_retries}")
        self.logger.info("")
        self.logger.info("Execution:")
        self.logger.info(f"  Multisig Script: {self.config.multisig_script}")
        self.logger.info(f"  Redeem Script: {'✓ Set' if self.config.redeem_script else '✗ Missing'}")
        self.logger.info(f"  Signer Keys: {'✓ Set' if self.config.signer1_wif and self.config.signer2_wif else '✗ Missing'}")
        self.logger.info("=== END CONFIGURATION ===")
    
    def run(self, command: str):
        try:
            self.load_environment()
            self.validate_configuration()
            self.logger.info("DevFund Manager initialized successfully")
            
            if command in ['dry-run', 'dryrun']:
                self.dry_run()
            elif command in ['config', 'show-config']:
                self.show_config()
            elif command in ['validate', 'check']:
                self.logger.info("All validations passed successfully")
            elif command == 'execute':
                if self.execute_distribution():
                    self.logger.info("Distribution execution completed successfully")
                else:
                    self.logger.error("Distribution execution failed")
                    sys.exit(1)
            elif command == 'check-ready':
                should_dist, _, _ = self.should_distribute()
                if should_dist:
                    self.logger.info("✓ Ready for distribution")
                    sys.exit(0)
                else:
                    self.logger.info("✗ Not ready for distribution")
                    sys.exit(1)
            else:
                raise ValueError(f"Unknown command: {command}")
                
        except (ValidationError, APIError, ValueError) as e:
            self.logger.error(f"Error: {e}")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="DevFund Manager for Junkcoin")
    parser.add_argument('command', choices=['dry-run', 'dryrun', 'config', 'show-config', 'validate', 'check', 'execute', 'check-ready'])
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    parser.add_argument('--confirm', action='store_true', help='Skip confirmation for execute command')
    
    args = parser.parse_args()
    
    # Safety check for execute command
    if args.command == 'execute' and not args.confirm:
        print("⚠️  WARNING: This will execute REAL transactions with REAL funds!")
        print("⚠️  Make sure you have:")
        print("   1. Tested with dry-run")
        print("   2. Verified all addresses are correct")
        print("   3. Confirmed your .env settings")
        print("   4. Confirmed minimum balance protection is set")
        print("")
        response = input("Are you absolutely sure you want to proceed? (type 'YES' to confirm): ")
        if response != 'YES':
            print("Execution cancelled for safety")
            sys.exit(1)
    
    config = Config()
    if args.log_level:
        config.log_level = args.log_level
    
    manager = DevFundManager(config)
    
    try:
        print(f"=== DevFund Manager - {args.command.title().replace('-', ' ')} Mode ===")
        manager.run(args.command)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)

if __name__ == "__main__":
    main()
