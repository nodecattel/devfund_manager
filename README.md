# DevFund Manager - Junkcoin Automated Distribution System

A comprehensive toolkit for managing Junkcoin development fund distributions with multisig security, automated monitoring, and efficient sendmany transactions.

## 🚀 Features

- **Automated Balance Monitoring** - Continuous monitoring with configurable thresholds
- **Multisig Security** - 2-of-3 multisig wallet protection
- **Efficient Sendmany** - Single transaction to multiple recipients (saves fees)
- **Minimum Balance Protection** - Preserves configurable minimum balance (default: 10 JKC)
- **Rate Limiting** - Prevents excessive distributions with hourly/daily limits
- **Emergency Controls** - Manual override and emergency stop functionality
- **Comprehensive Logging** - Detailed audit trail with flexible log levels
- **UTXO Management** - Automatic consolidation to prevent fragmentation

## 📁 Project Structure

```
devfund_manager/
├── devfund_manager.py      # Main distribution manager
├── multisig_op.py          # Multisig transaction operations
├── monitor.py              # Automated monitoring daemon
├── .env                    # Configuration file
├── .env.example            # Configuration template
├── README.md               # This file
├── monitor.log             # Monitor activity logs
├── devfund_manager.log     # Distribution logs
└── .monitor_state.json     # Monitor state persistence
```

## 🛠️ Installation

### Prerequisites

- **Python 3.7+** with required packages:
  ```bash
  pip install requests python-dotenv
  ```

- **Junkcoin Core** with RPC enabled:
  ```bash
  # In junkcoin.conf
  server=1
  rpcuser=your_rpc_user
  rpcpassword=your_rpc_password
  rpcallowip=127.0.0.1
  ```

- **junkcoin-cli** accessible in PATH or specify custom path

### Setup

1. **Clone/Download** the DevFund Manager files to your server

2. **Make scripts executable:**
   ```bash
   chmod +x devfund_manager.py multisig_op.py monitor.py
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   nano .env  # Edit with your settings
   ```

## ⚙️ Configuration

### Required `.env` Variables

```bash
# Multisig Configuration
DEVFUND_ADDRESS=3YourMultisigAddressHere123456789ABC
REDEEM_SCRIPT=your_hex_encoded_redeem_script_here

# Private Keys (WIF format, 52 characters starting with 'N')
SIGNER1_WIF=NYourFirstPrivateKeyInWIFFormatHere123456789
SIGNER2_WIF=NYourSecondPrivateKeyInWIFFormatHere123456789

# Distribution Addresses
LIQUIDITY_ADDRESS=7YourLiquidityAddressHere123456789ABC
DEV_ADDRESS=7YourDevelopmentAddressHere123456789ABC
MARKETING_ADDRESS=7YourMarketingAddressHere123456789ABC

# Distribution Percentages (must sum to 100)
LIQUIDITY_PERCENT=50
DEV_PERCENT=25
MARKETING_PERCENT=25

# Thresholds
THRESHOLD_BALANCE_SATS=1000000000    # 10 JKC minimum for distribution
MINIMUM_BALANCE_SATS=1000000000      # 10 JKC always preserved in wallet

# Optional: Advanced Settings
USE_SENDMANY=true                    # Use efficient single transaction
LOG_LEVEL=DEBUG,WARN,ERROR          # Flexible logging levels
FEE_RATE=10                          # Satoshis per vbyte
```

### Log Level Options

```bash
# Single level
LOG_LEVEL=INFO                    # Clean production display
LOG_LEVEL=DEBUG                   # Full debugging details
LOG_LEVEL=ERROR                   # Only actual errors

# Multiple levels (comma-separated)
LOG_LEVEL=DEBUG,WARN,ERROR        # Skip INFO, show debug + problems
LOG_LEVEL=WARN,ERROR             # Only warnings and errors
LOG_LEVEL=INFO,ERROR             # Important info + errors only
```

## 🎯 Usage

### Manual Operations

#### Check Configuration
```bash
./devfund_manager.py validate
./devfund_manager.py show-config
```

#### Test Distribution (Dry Run)
```bash
./devfund_manager.py dry-run
```
**Sample Output:**
```
Current state:
  Address: 3YourMultisigAddressHere123456789ABC
  UTXOs: 1 (threshold: 1)
  Balance: 69 JKC (threshold: 10 JKC)
  Minimum balance: 10 JKC (will be preserved)
✓ Thresholds met - distribution would occur

Distribution that would occur:
  Liquidity (50%): 29.4999875 JKC → 7YourLiquidityAddressHere123456789ABC
  Dev (25%): 14.74999375 JKC → 7YourDevelopmentAddressHere123456789ABC
  Marketing (25%): 14.74999375 JKC → 7YourMarketingAddressHere123456789ABC
  Method: Single sendmany transaction (efficient)
```

#### Execute Distribution
```bash
./devfund_manager.py execute
```

#### Check Readiness
```bash
./devfund_manager.py check-ready
echo $?  # 0 = ready, 1 = not ready
```

### Multisig Operations

#### Validate Setup
```bash
./multisig_op.py validate
```

#### Single Transaction
```bash
./multisig_op.py auto \
  3YourMultisigAddressHere123456789ABC \
  $REDEEM_SCRIPT \
  1.5 \
  7YourRecipientAddressHere123456789ABC \
  $SIGNER1_WIF \
  $SIGNER2_WIF
```

#### Multiple Recipients (Sendmany)
```bash
./multisig_op.py sendmany \
  3YourMultisigAddressHere123456789ABC \
  $REDEEM_SCRIPT \
  '{"7RecipientAddr1Here123456789ABC":"1.0","7RecipientAddr2Here123456789ABC":"2.0"}' \
  $SIGNER1_WIF \
  $SIGNER2_WIF
```

### Automated Monitoring

#### Start Monitoring Daemon
```bash
./monitor.py start
```
**Output:**
```
🚀 Starting DevFund Monitor...
📝 Logs: /path/to/monitor.log
🛑 Emergency stop: touch .emergency_stop
Press Ctrl+C to stop
```

#### Monitor Status
```bash
./monitor.py status
```
**Sample Output:**
```
📊 DevFund Monitor Status:
  Last distribution: 2025-06-03T21:22:07
  Distributions today: 1
  Total distributions: 42
  Last consolidation: Never
  Total consolidations: 0
  Error count: 0
✅ Running normally
```

#### Single Check (Testing)
```bash
./monitor.py once
```

#### Emergency Stop
```bash
./monitor.py stop
# or manually: touch .emergency_stop
```

#### Custom Monitor Settings
```bash
./monitor.py start \
  --check-interval 600 \
  --max-distributions-per-hour 2 \
  --max-distributions-per-day 12
```

## 🔒 Security Features

### Multisig Protection
- **2-of-3 Multisig** wallet requiring 2 signatures
- **Private keys** stored securely in `.env` (600 permissions)
- **Redeem script** validation before transactions

### Rate Limiting
- **Hourly limit:** 1 distribution per hour (configurable)
- **Daily limit:** 6 distributions per day (configurable)
- **Emergency stop:** Immediate pause capability

### Balance Protection
- **Minimum balance** always preserved (10 JKC default)
- **Fee estimation** with safety margins
- **Dust threshold** protection

### Audit Trail
- **Complete logging** of all operations
- **State persistence** across restarts
- **Transaction IDs** recorded for verification

## 🎛️ Advanced Configuration

### Custom Fee Rates
```bash
# In .env
FEE_RATE=20  # Higher fee for faster confirmation
```

### API Endpoints
```bash
# Primary and fallback APIs
PRIMARY_API=https://api.junkcoin.org
FALLBACK_API=https://junk-api.s3na.xyz
```

### Monitor Customization
```bash
# Check every 10 minutes
./monitor.py start --check-interval 600

# More aggressive distribution
./monitor.py start --max-distributions-per-hour 3 --max-distributions-per-day 20

# Disable consolidation
./monitor.py start --no-consolidate
```

## 📊 Monitoring & Logs

### Log Files
- **`devfund_manager.log`** - Distribution operations
- **`monitor.log`** - Automated monitoring activity
- **`.monitor_state.json`** - Monitor state persistence

### Log Rotation
- **Automatic rotation** at 10MB (devfund) / 50MB (monitor)
- **Retention:** 5-30 files depending on configuration
- **Permissions:** 600 (owner read/write only)

### Status Monitoring
```bash
# Watch live monitor logs
tail -f monitor.log

# Check recent distributions
grep "Distribution completed" devfund_manager.log

# Monitor state inspection
cat .monitor_state.json | jq .
```

## 🚨 Emergency Procedures

### Emergency Stop
```bash
# Immediate stop (next check cycle)
touch .emergency_stop

# or via command
./monitor.py stop
```

### Manual Override
```bash
# Force distribution regardless of thresholds
./devfund_manager.py execute --confirm

# Check balance without API
junkcoin-cli listunspent 1 9999999 '["34P2otqp4hUL4kRoVH74KpyrBdkrqZM18n"]'
```

### Recovery Procedures
```bash
# Reset monitor state
rm .monitor_state.json

# Clear emergency stop
rm .emergency_stop

# Validate all components
./multisig_op.py validate
./devfund_manager.py validate
```

## 🔧 Troubleshooting

### Common Issues

#### "Method not found" Warnings
**Cause:** Using older junkcoin-cli version  
**Solution:** These are normal warnings, script automatically falls back to compatible methods

#### "WIF key must be 52 characters"
**Cause:** Incorrect WIF format  
**Solution:** Ensure WIF keys start with 'N' and are exactly 52 characters

#### "Cannot connect to junkcoin daemon"
**Cause:** junkcoin-cli not running or misconfigured  
**Solution:** 
```bash
# Check daemon status
junkcoin-cli getblockchaininfo

# Restart if needed
junkcoind -daemon
```

#### Distribution not triggering
**Cause:** Thresholds not met or rate limits active  
**Solution:**
```bash
# Check readiness
./devfund_manager.py check-ready

# Check monitor status
./monitor.py status

# Review dry-run output
./devfund_manager.py dry-run
```

### Debug Mode
```bash
# Enable debug logging
LOG_LEVEL=DEBUG ./devfund_manager.py dry-run

# Monitor with debug
LOG_LEVEL=DEBUG ./monitor.py once
```

## 📈 Performance

### Transaction Efficiency
- **Sendmany:** 1 transaction for 3 distributions vs 3 separate transactions
- **Fee savings:** ~67% reduction in transaction fees
- **Network efficiency:** Reduced blockchain bloat

### Monitoring Performance
- **CPU usage:** Minimal (5-minute checks)
- **Memory:** ~20MB Python process
- **Disk:** Log files with automatic rotation
- **Network:** Periodic API calls for balance checking

## 🤝 Contributing

### Development Setup
```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/

# Code formatting
black *.py
```

### Adding Features
1. **Fork** the repository
2. **Create feature branch**
3. **Add tests** for new functionality
4. **Update documentation**
5. **Submit pull request**

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This software handles real cryptocurrency transactions. Always:
- **Test thoroughly** on testnet first
- **Backup private keys** securely
- **Monitor operations** regularly
- **Use emergency stops** if needed
- **Verify all addresses** before deployment

The authors are not responsible for any financial losses resulting from the use of this software.

## 📞 Support

- **Issues:** Create GitHub issue with detailed description
- **Security:** Report security issues privately
- **Documentation:** Check this README and inline code comments
- **Community:** Join Junkcoin community channels

---

**Version:** 1.0.0  
**Last Updated:** June 2025  
**Compatibility:** Junkcoin Core 0.16+, Python 3.7+
