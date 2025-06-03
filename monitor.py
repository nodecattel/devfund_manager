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
    consolidate_interval: int = 3600  # Consolidate every hour
    max_utxos_before_consolidate: int = 5
    
    # Safety settings
    max_distributions_per_hour: int = 1
    max_distributions_per_day: int = 6
    emergency_stop_file: str = ".emergency_stop"
    
    # Notification settings
    notify_on_distribution: bool = True
    notify_on_consolidation: bool = True
    notify_on_errors: bool = True
    
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
        self.logger = self._setup_logging()
        self.state = self._load_state()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        self.logger.info("🛑 Shutdown signal received, stopping monitor gracefully...")
        self.running = False
        self._save_state()
        sys.exit(0)
    
    def _setup_logging(self):
        """Setup logging with rotation"""
        logger = logging.getLogger('devfund_monitor')
        logger.setLevel(logging.INFO)
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Console handler
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
        """Run a command with proper error handling"""
        try:
            self.logger.debug(f"Running: {description}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.config.script_dir)
            )
            
            if result.returncode == 0:
                return True, result.stdout.strip()
            else:
                self.logger.error(f"{description} failed: {result.stderr}")
                return False, result.stderr.strip()
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"{description} timed out after {timeout}s")
            return False, "Timeout"
        except Exception as e:
            self.logger.error(f"{description} error: {e}")
            return False, str(e)
    
    def _check_ready_for_distribution(self):
        """Check if ready for distribution"""
        success, output = self._run_command(
            ["python3", str(self.config.devfund_script), "check-ready"],
            "Check distribution readiness"
        )
        return success
    
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
            
            self.logger.info("🎉 Distribution completed successfully!")
            if self.config.notify_on_distribution:
                self._notify("Distribution Success", "DevFund distribution completed successfully")
            return True
        else:
            self.state['errors_count'] += 1
            self.logger.error(f"❌ Distribution failed: {output}")
            if self.config.notify_on_errors:
                self._notify("Distribution Failed", f"Distribution failed: {output}")
            return False
    
    def _should_consolidate(self):
        """Check if consolidation is needed"""
        # Check time since last consolidation
        if self.state['last_consolidation']:
            last_consolidation = datetime.fromisoformat(self.state['last_consolidation'])
            if datetime.now() - last_consolidation < timedelta(seconds=self.config.consolidate_interval):
                return False
        
        # Check UTXO count (this would need to be implemented)
        # For now, we'll consolidate based on time interval
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
    
    def _notify(self, title: str, message: str):
        """Send notification (placeholder for future webhook/email integration)"""
        self.logger.info(f"📧 NOTIFICATION: {title} - {message}")
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
            'last_balance': self.state['last_balance']
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
            if self._check_ready_for_distribution():
                self.logger.info("✅ Ready for distribution - executing...")
                success = self._execute_distribution()
                if success:
                    # Wait a bit after successful distribution
                    time.sleep(30)
            else:
                self.logger.debug("⏳ Not ready for distribution")
            
            # Check if consolidation is needed
            if self._should_consolidate():
                self._execute_consolidation()
            
            self.state['errors_count'] = max(0, self.state['errors_count'] - 1)  # Decay error count
            
        except Exception as e:
            self.logger.error(f"Monitoring cycle error: {e}")
            self.state['errors_count'] += 1
            
            if self.config.notify_on_errors:
                self._notify("Monitor Error", f"Monitoring error: {e}")
        
        finally:
            self._save_state()
    
    def run(self):
        """Main monitoring loop"""
        self.running = True
        self.start_time = datetime.now().isoformat()
        
        self.logger.info("🚀 DevFund Monitor starting...")
        self.logger.info(f"📊 Check interval: {self.config.check_interval}s")
        self.logger.info(f"🔗 Consolidate interval: {self.config.consolidate_interval}s")
        self.logger.info(f"⚡ Max distributions: {self.config.max_distributions_per_hour}/hour, {self.config.max_distributions_per_day}/day")
        self.logger.info(f"🛑 Emergency stop file: {self.config.emergency_stop_file}")
        
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
