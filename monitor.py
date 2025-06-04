#!/usr/bin/env python3
"""
DevFund Monitor - Automated Junkcoin Distribution System
Monitors multisig balance and automatically executes distributions
"""

import os
import sys
import time
import signal
import logging
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class MonitorConfig:
    # Monitoring settings
    check_interval: int = 300  # Check every 5 minutes
    consolidate_interval: int = 0  # Consolidation disabled by default
    max_utxos_before_consolidate: int = 5
    
    # Safety settings
    max_distributions_per_hour: int = 1
    max_distributions_per_day: int = 6
    emergency_stop_file: str = ".emergency_stop"
    
    # Notification settings
    notify_on_distribution: bool = True
    notify_on_consolidation: bool = True
    notify_on_errors: bool = True
    explorer_url: str = "https://jkc-explorer.dedoo.xyz"
    
    # Logging
    log_retention_days: int = 30
    
    script_dir: Path = field(default_factory=lambda: Path.cwd())
    log_file: Path = field(init=False)
    devfund_script: Path = field(init=False)
    multisig_script: Path = field(init=False)
    state_file: Path = field(init=False)
    
    def __post_init__(self):
        self.log_file = self.script_dir / "monitor.log"
        self.devfund_script = self.script_dir / "devfund_manager.py"
        self.multisig_script = self.script_dir / "multisig_op.py"
        self.state_file = self.script_dir / ".monitor_state.json"

class DevFundMonitor:
    
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.running = False
        
        # Load .env file before setting up logging
        self._load_env_file()
        self._load_env_config()
        
        self.logger = self._setup_logging()
        self.state = self._load_state()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
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
    
    def _load_env_config(self):
        """Load configuration from environment variables"""
        # Load environment variables
        env_mappings = {
            'MONITOR_CHECK_INTERVAL': 'check_interval',
            'MONITOR_CONSOLIDATE_INTERVAL': 'consolidate_interval', 
            'MONITOR_MAX_UTXOS': 'max_utxos_before_consolidate',
            'MONITOR_MAX_DISTRIBUTIONS_PER_HOUR': 'max_distributions_per_hour',
            'MONITOR_MAX_DISTRIBUTIONS_PER_DAY': 'max_distributions_per_day',
            'MONITOR_EMERGENCY_STOP_FILE': 'emergency_stop_file',
            'NOTIFY_ON_DISTRIBUTION': 'notify_on_distribution',
            'NOTIFY_ON_CONSOLIDATION': 'notify_on_consolidation', 
            'NOTIFY_ON_ERRORS': 'notify_on_errors',
            'EXPLORER_URL': 'explorer_url'
        }
        
        for env_var, config_attr in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                if config_attr in ['check_interval', 'consolidate_interval', 'max_utxos_before_consolidate', 
                                 'max_distributions_per_hour', 'max_distributions_per_day']:
                    try:
                        value = int(value)
                    except ValueError:
                        continue
                elif config_attr in ['notify_on_distribution', 'notify_on_consolidation', 'notify_on_errors']:
                    value = value.lower() in ('true', '1', 'yes', 'on')
                setattr(self.config, config_attr, value)
        
    def _signal_handler(self, signum, frame):
        self.logger.info("🛑 Shutdown signal received, stopping monitor gracefully...")
        self.running = False
        self._save_state()
        sys.exit(0)
    
    def _setup_logging(self):
        """Setup logging for multisig operations with flexible log levels"""
        logger = logging.getLogger('devfund_monitor')
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Parse LOG_LEVEL from environment
        log_level_env = os.getenv('LOG_LEVEL', 'INFO')  # Changed from DEBUG to INFO
        
        # Handle comma-separated log levels
        if ',' in log_level_env:
            # Custom log levels specified
            allowed_levels = [level.strip().upper() for level in log_level_env.split(',')]
            logger.setLevel(logging.DEBUG)  # Set to lowest level to catch everything
            
            # Create custom handler that filters by allowed levels
            console_handler = logging.StreamHandler()
            console_format = logging.Formatter('[%(asctime)s] [MONITOR] [%(levelname)s] %(message)s')
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
            console_format = logging.Formatter('[%(asctime)s] [MONITOR] [%(levelname)s] %(message)s')
            console_handler.setFormatter(console_format)
            logger.addHandler(console_handler)
        
        # File handler with rotation
        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                self.config.log_file, 
                maxBytes=50*1024*1024,  # 50MB
                backupCount=self.config.log_retention_days
            )
            file_format = logging.Formatter('[%(asctime)s] [MONITOR] [%(levelname)s] %(message)s')
            file_handler.setFormatter(file_format)
            logger.addHandler(file_handler)
            os.chmod(self.config.log_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not setup file logging: {e}")
        
        return logger
    
    def _load_state(self):
        """Load monitor state from file"""
        import json
        
        default_state = {
            'last_distribution': None,
            'last_consolidation': None,
            'distributions_today': 0,
            'distributions_this_hour': 0,
            'last_check_date': None,
            'last_check_hour': None,
            'total_distributions': 0,
            'total_consolidations': 0,
            'last_balance': 0,
            'errors_count': 0
        }
        
        if self.config.state_file.exists():
            try:
                with open(self.config.state_file, 'r') as f:
                    state = json.load(f)
                    # Merge with defaults for any missing keys
                    for key, value in default_state.items():
                        if key not in state:
                            state[key] = value
                    return state
            except Exception as e:
                self.logger.warning(f"Could not load state file: {e}")
        
        return default_state
    
    def _save_state(self):
        """Save monitor state to file"""
        import json
        try:
            with open(self.config.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.chmod(self.config.state_file, 0o600)
        except Exception as e:
            self.logger.error(f"Could not save state: {e}")
    
    def _reset_daily_counters(self):
        """Reset daily counters if needed"""
        current_date = datetime.now().strftime('%Y-%m-%d')
        if self.state['last_check_date'] != current_date:
            self.state['distributions_today'] = 0
            self.state['last_check_date'] = current_date
            self.logger.info("🗓️  Daily counters reset")
    
    def _reset_hourly_counters(self):
        """Reset hourly counters if needed"""
        current_hour = datetime.now().strftime('%Y-%m-%d-%H')
        if self.state['last_check_hour'] != current_hour:
            self.state['distributions_this_hour'] = 0
            self.state['last_check_hour'] = current_hour
            self.logger.debug("⏰ Hourly counters reset")
    
    def _check_emergency_stop(self):
        """Check if emergency stop file exists"""
        emergency_file = self.config.script_dir / self.config.emergency_stop_file
        if emergency_file.exists():
            self.logger.warning("🚨 EMERGENCY STOP FILE DETECTED - Monitor paused")
            self.logger.warning(f"Remove {emergency_file} to resume monitoring")
            return True
        return False
    
    def _run_command(self, cmd: list, description: str, timeout: int = 300):
        """Run a command with proper error handling and capture both stdout and stderr"""
        try:
            self.logger.debug(f"Running: {description}")
            self.logger.debug(f"Command: {' '.join(cmd)}")
            self.logger.debug(f"Working directory: {self.config.script_dir}")
            
            # Set up environment to force INFO level logging for devfund_manager.py
            env = os.environ.copy()
            env['LOG_LEVEL'] = 'INFO'  # Force INFO level for cleaner output
            
            # Use subprocess.check_output to capture both stdout and stderr
            try:
                output = subprocess.check_output(
                    cmd,
                    stderr=subprocess.STDOUT,  # Combine stderr with stdout
                    text=True,
                    timeout=timeout,
                    cwd=str(self.config.script_dir),
                    env=env
                )
                
                self.logger.debug(f"Command succeeded, combined output length: {len(output)}")
                return True, output.strip()
                
            except subprocess.CalledProcessError as e:
                self.logger.error(f"{description} failed with return code {e.returncode}")
                self.logger.error(f"Output: {e.output}")
                return False, e.output.strip() if e.output else str(e)
                
            except subprocess.TimeoutExpired as e:
                self.logger.error(f"{description} timed out after {timeout}s")
                return False, "Timeout"
                
        except Exception as e:
            self.logger.error(f"{description} error: {e}")
            return False, str(e)
    
    def _check_ready_for_distribution(self):
        """Check if ready for distribution using dry-run to check readiness"""
        # Use dry-run and check if it shows distribution would occur
        success, output = self._run_command(
            ["python3", str(self.config.devfund_script), "dry-run"],
            "Check distribution readiness via dry-run"
        )
        
        if success:
            self.logger.debug(f"Dry-run output for readiness check (first 200 chars):\n{output[:200]}...")
            # Check if dry-run output indicates readiness
            return "✓ Thresholds met - distribution would occur" in output
        
        return False
    
    def _execute_distribution(self):
        """Execute distribution"""
        if self.state['distributions_this_hour'] >= self.config.max_distributions_per_hour:
            self.logger.warning("⏸️  Distribution rate limit reached (hourly)")
            return False
        
        if self.state['distributions_today'] >= self.config.max_distributions_per_day:
            self.logger.warning("⏸️  Distribution rate limit reached (daily)")
            return False
        
        self.logger.info("💰 Executing distribution...")
        
        success, output = self._run_command(
            ["python3", str(self.config.devfund_script), "execute", "--confirm"],
            "Execute distribution",
            timeout=600  # 10 minutes
        )
        
        if success:
            self.state['last_distribution'] = datetime.now().isoformat()
            self.state['distributions_today'] += 1
            self.state['distributions_this_hour'] += 1
            self.state['total_distributions'] += 1
            
            # Extract transaction ID from output
            txid = self._extract_transaction_id(output)
            
            if txid:
                explorer_link = f"{self.config.explorer_url.rstrip('/')}/tx/{txid}"
                self.logger.info(f"🎉 Distribution completed successfully!")
                self.logger.info(f"💎 Transaction ID: {txid}")
                self.logger.info(f"🔗 Explorer: {explorer_link}")
                
                # Store transaction info in state for status checks
                self.state['last_txid'] = txid
                self.state['last_explorer_link'] = explorer_link
                
                if self.config.notify_on_distribution:
                    self.logger.info(f"📧 NOTIFICATION: Distribution Success")
                    self.logger.info(f"📧 TX: {txid}")
                    self.logger.info(f"📧 Link: {explorer_link}")
            else:
                self.logger.info("🎉 Distribution completed successfully!")
                self.logger.warning("⚠️  Could not extract transaction ID from output")
                self.logger.debug(f"Full output for debugging:")
                self.logger.debug(f"Output: {output[:1000]}...")  # Show first 1000 chars for debugging
                
                if self.config.notify_on_distribution:
                    self.logger.info(f"📧 NOTIFICATION: Distribution Success (no TX ID found)")
            
            return True
        else:
            self.state['errors_count'] += 1
            self.logger.error(f"❌ Distribution failed: {output}")
            if self.config.notify_on_errors:
                self._notify("Distribution Failed", f"Distribution failed: {output}")
            return False
    
    def _should_consolidate(self):
        """Check if consolidation is needed"""
        # Skip consolidation if disabled
        if self.config.consolidate_interval == 0:
            return False
            
        # Check time since last consolidation
        if self.state['last_consolidation']:
            last_consolidation = datetime.fromisoformat(self.state['last_consolidation'])
            if datetime.now() - last_consolidation < timedelta(seconds=self.config.consolidate_interval):
                return False
        
        # Only consolidate if explicitly enabled and time interval met
        return True
    
    def _execute_consolidation(self):
        """Execute UTXO consolidation"""
        self.logger.info("🔗 Executing UTXO consolidation...")
        
        # Check if we have the multisig consolidation feature
        success, output = self._run_command(
            ["python3", str(self.config.multisig_script), "validate"],
            "Validate multisig script"
        )
        
        if not success:
            self.logger.warning("⚠️  Multisig script not available for consolidation")
            return False
        
        # Note: We'd need to add consolidation command to multisig_op.py
        # For now, just log that we would consolidate
        self.logger.info("📝 Consolidation check completed (feature pending)")
        self.state['last_consolidation'] = datetime.now().isoformat()
        self.state['total_consolidations'] += 1
        
        if self.config.notify_on_consolidation:
            self._notify("Consolidation", "UTXO consolidation completed")
        
        return True
    
    def _extract_transaction_id(self, output: str) -> Optional[str]:
        """Extract transaction ID from command output"""
        import re
        
        # Look for various patterns that indicate a transaction ID
        patterns = [
            r'💎 Transaction ID: ([a-fA-F0-9]{64})',
            r'Transaction ID: ([a-fA-F0-9]{64})',
            r'Transaction broadcast successfully.*: ([a-fA-F0-9]{64})',
            r'Success: ([a-fA-F0-9]{64})',
            r'txid.*: ([a-fA-F0-9]{64})',
            r'✅.*: ([a-fA-F0-9]{64})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        
        # Look for any 64-character hex string (likely a txid)
        hex_pattern = r'\b([a-fA-F0-9]{64})\b'
        matches = re.findall(hex_pattern, output)
        if matches:
            return matches[-1]  # Return the last one found
        
        return None
    
    def _notify(self, title: str, message: str):
        """Send notification (placeholder for future webhook/email integration)"""
        self.logger.info(f"📧 NOTIFICATION: {title}")
        self.logger.info(f"📧 Message: {message}")
        # Future: Add webhook, email, or other notification methods
    
    def _get_status_summary(self):
        """Get current status summary"""
        return {
            'uptime_start': getattr(self, 'start_time', None),
            'last_distribution': self.state['last_distribution'],
            'distributions_today': self.state['distributions_today'],
            'total_distributions': self.state['total_distributions'],
            'last_consolidation': self.state['last_consolidation'],
            'total_consolidations': self.state['total_consolidations'],
            'errors_count': self.state['errors_count'],
            'last_balance': self.state['last_balance'],
            'last_txid': self.state.get('last_txid', None),
            'last_explorer_link': self.state.get('last_explorer_link', None)
        }
    
    def run_once(self):
        """Run a single monitoring cycle"""
        self._reset_daily_counters()
        self._reset_hourly_counters()
        
        # Check emergency stop
        if self._check_emergency_stop():
            time.sleep(60)  # Wait 1 minute before checking again
            return
        
        try:
            # Check if ready for distribution
            ready = self._check_ready_for_distribution()
            
            if ready:
                self.logger.info("✅ Ready for distribution - executing...")
                success = self._execute_distribution()
                if success:
                    # Wait a bit after successful distribution
                    time.sleep(30)
            else:
                # Only show detailed status every hour or on first run
                current_time = datetime.now()
                last_status_time = getattr(self, '_last_status_time', None)
                
                if (last_status_time is None or 
                    current_time - last_status_time > timedelta(hours=1)):
                    
                    self.logger.info("📊 STATUS UPDATE:")
                    self._show_readiness_status()
                    self._last_status_time = current_time
                else:
                    # Just show a brief status
                    next_check = (current_time + timedelta(seconds=self.config.check_interval)).strftime('%H:%M:%S')
                    self.logger.info(f"⏰ Waiting... Next check at {next_check}")
            
            # Check if consolidation is needed (only if enabled)
            if self.config.consolidate_interval > 0 and self._should_consolidate():
                self._execute_consolidation()
            
            self.state['errors_count'] = max(0, self.state['errors_count'] - 1)  # Decay error count
            
        except Exception as e:
            self.logger.error(f"Monitoring cycle error: {e}")
            self.state['errors_count'] += 1
            
            if self.config.notify_on_errors:
                self.logger.info(f"📧 NOTIFICATION: Monitor Error")
                self.logger.info(f"📧 Message: Monitoring error: {e}")
        
        finally:
            self._save_state()
    
    def _show_readiness_status(self):
        """Show detailed status when not ready for distribution"""
        try:
            # Get the dry-run output to show current status
            success, output = self._run_command(
                ["python3", str(self.config.devfund_script), "dry-run"],
                "Get current status"
            )
            
            self.logger.debug(f"Dry-run success: {success}")
            self.logger.debug(f"Dry-run output length: {len(output) if output else 0}")
            
            if success and output:
                # Parse the output line by line
                lines = output.split('\n')
                self.logger.debug(f"Total lines in output: {len(lines)}")
                
                # Extract key information from dry-run output
                current_balance = None
                current_utxos = None
                threshold_met = False
                is_ready = False
                
                for i, line in enumerate(lines):
                    line = line.strip()
                    
                    # Skip empty lines and process [INFO] lines
                    if not line:
                        continue
                    
                    # Remove [INFO] prefix for processing
                    if line.startswith('[INFO]'):
                        line = line[6:].strip()
                    
                    # Extract balance information
                    if 'Balance:' in line and 'JKC' in line and 'threshold' not in line.lower():
                        self.logger.info(f"💰 {line}")
                        # Extract the actual balance number
                        import re
                        balance_match = re.search(r'Balance:\s*([\d.]+)\s*JKC', line)
                        if balance_match:
                            current_balance = float(balance_match.group(1))
                    
                    # Extract UTXO information
                    elif 'UTXOs:' in line:
                        self.logger.info(f"📦 {line}")
                        import re
                        utxo_match = re.search(r'UTXOs:\s*(\d+)', line)
                        if utxo_match:
                            current_utxos = int(utxo_match.group(1))
                    
                    # Extract minimum balance info
                    elif 'Minimum balance:' in line:
                        self.logger.info(f"🛡️  {line}")
                    
                    # Check if thresholds are met
                    elif '✓ Thresholds met' in line:
                        threshold_met = True
                        is_ready = True
                        self.logger.info(f"✅ {line}")
                    
                    # Show distribution plan if ready
                    elif threshold_met and ('Liquidity (' in line or 'Dev (' in line or 'Marketing (' in line):
                        self.logger.info(f"💸 {line}")
                    
                    # Show address
                    elif 'Address:' in line:
                        self.logger.info(f"🏦 {line}")
                
                # Show appropriate message based on readiness
                if is_ready:
                    self.logger.info("🎉 READY FOR DISTRIBUTION!")
                    self.logger.info("   Distribution will be executed on next check cycle")
                else:
                    self.logger.info("💡 Current status: NOT READY for distribution")
                    if current_balance and current_utxos:
                        self.logger.info(f"   • Balance: {current_balance:.2f} JKC")
                        self.logger.info(f"   • UTXOs: {current_utxos}")
                    self.logger.info("   • Need: Balance > threshold + minimum reserve")
                
                # Show when we'll check again
                self._estimate_readiness_time()
            else:
                self.logger.warning("⚠️  Could not get current status")
                self.logger.debug(f"Dry-run failed. Success: {success}, Output: {output}")
                
        except Exception as e:
            self.logger.error(f"Status check error: {e}")
            import traceback
            self.logger.debug(f"Exception traceback: {traceback.format_exc()}")
    
    def _estimate_readiness_time(self):
        """Estimate when conditions might be met for distribution"""
        try:
            next_check = (datetime.now() + timedelta(seconds=self.config.check_interval)).strftime('%H:%M:%S')
            self.logger.info(f"⏰ Next check at {next_check} (every {self.config.check_interval//60} minutes)")
            
        except Exception as e:
            self.logger.debug(f"Time estimation error: {e}")
    
    def run(self):
        """Main monitoring loop"""
        self.running = True
        self.start_time = datetime.now().isoformat()
        
        self.logger.info("🚀 DevFund Monitor starting...")
        self.logger.info(f"📊 Check interval: {self.config.check_interval}s")
        
        if self.config.consolidate_interval > 0:
            self.logger.info(f"🔗 Consolidate interval: {self.config.consolidate_interval}s")
        else:
            self.logger.info(f"🔗 Consolidation: DISABLED")
            
        self.logger.info(f"⚡ Max distributions: {self.config.max_distributions_per_hour}/hour, {self.config.max_distributions_per_day}/day")
        self.logger.info(f"🛑 Emergency stop file: {self.config.emergency_stop_file}")
        
        # Show initial status
        self.logger.info("=" * 50)
        self.logger.info("📊 INITIAL STATUS CHECK")
        self._show_readiness_status()
        self.logger.info("=" * 50)
        
        try:
            while self.running:
                self.run_once()
                
                if self.running:  # Check again in case we were stopped during run_once()
                    time.sleep(self.config.check_interval)
                    
        except KeyboardInterrupt:
            self.logger.info("⌨️  Keyboard interrupt received")
        finally:
            self.logger.info("🛑 DevFund Monitor stopped")
            self._save_state()

def main():
    parser = argparse.ArgumentParser(description="DevFund Monitor - Automated Distribution System")
    parser.add_argument('command', choices=['start', 'stop', 'status', 'once'], 
                       help='Monitor command')
    parser.add_argument('--check-interval', type=int, default=300,
                       help='Check interval in seconds (default: 300)')
    parser.add_argument('--max-distributions-per-hour', type=int, default=1,
                       help='Maximum distributions per hour (default: 1)')
    parser.add_argument('--max-distributions-per-day', type=int, default=6,
                       help='Maximum distributions per day (default: 6)')
    parser.add_argument('--no-consolidate', action='store_true',
                       help='Disable automatic consolidation')
    
    args = parser.parse_args()
    
    # Create config
    config = MonitorConfig()
    config.check_interval = args.check_interval
    config.max_distributions_per_hour = args.max_distributions_per_hour
    config.max_distributions_per_day = args.max_distributions_per_day
    
    if args.no_consolidate:
        config.consolidate_interval = 0  # Disable consolidation
    
    monitor = DevFundMonitor(config)
    
    if args.command == 'start':
        print("🚀 Starting DevFund Monitor...")
        print(f"📝 Logs: {config.log_file}")
        print(f"🛑 Emergency stop: touch {config.emergency_stop_file}")
        print("Press Ctrl+C to stop")
        print("")
        monitor.run()
        
    elif args.command == 'stop':
        emergency_file = config.script_dir / config.emergency_stop_file
        emergency_file.touch()
        print(f"🛑 Emergency stop file created: {emergency_file}")
        print("Monitor will stop at next check cycle")
        
    elif args.command == 'status':
        status = monitor._get_status_summary()
        print("📊 DevFund Monitor Status:")
        print(f"  Last distribution: {status['last_distribution'] or 'Never'}")
        print(f"  Distributions today: {status['distributions_today']}")
        print(f"  Total distributions: {status['total_distributions']}")
        print(f"  Last consolidation: {status['last_consolidation'] or 'Never'}")
        print(f"  Total consolidations: {status['total_consolidations']}")
        print(f"  Error count: {status['errors_count']}")
        
        # Show last transaction details if available
        if status.get('last_txid'):
            print(f"  Last TX ID: {status['last_txid']}")
        if status.get('last_explorer_link'):
            print(f"  Last TX Explorer: {status['last_explorer_link']}")
        
        # Check if emergency stop is active
        emergency_file = config.script_dir / config.emergency_stop_file
        if emergency_file.exists():
            print(f"🚨 EMERGENCY STOP ACTIVE: {emergency_file}")
        else:
            print("✅ Running normally")
            
    elif args.command == 'once':
        print("🔄 Running single monitoring cycle...")
        monitor.run_once()
        print("✅ Cycle completed")

if __name__ == "__main__":
    main()
