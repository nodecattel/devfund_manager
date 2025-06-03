#!/bin/bash

PRIMARY_API="https://junk-api.s3na.xyz"
DEVFUND_ADDRESS="34P2otqp4hUL4kRoVH74KpyrBdkrqZM18n"

echo "Testing UTXO API call..."
utxo_url="$PRIMARY_API/address/$DEVFUND_ADDRESS/utxo?limit=10000"
echo "URL: $utxo_url"

utxo_result=$(curl -s --connect-timeout 30 --max-time 30 "$utxo_url" 2>&1)
echo "UTXO Result: $utxo_result"

if echo "$utxo_result" | jq . >/dev/null 2>&1; then
    echo "✅ UTXO JSON is valid"
    utxo_count=$(echo "$utxo_result" | jq '.total // 0')
    echo "UTXO Count: $utxo_count"
else
    echo "❌ UTXO JSON is invalid"
fi

echo -e "\nTesting Balance API call..."
balance_url="$PRIMARY_API/address/$DEVFUND_ADDRESS"
echo "URL: $balance_url"

balance_result=$(curl -s --connect-timeout 30 --max-time 30 "$balance_url" 2>&1)
echo "Balance Result: $balance_result"

if echo "$balance_result" | jq . >/dev/null 2>&1; then
    echo "✅ Balance JSON is valid"
    funded=$(echo "$balance_result" | jq '.chain_stats.funded_txo_sum // 0')
    spent=$(echo "$balance_result" | jq '.chain_stats.spent_txo_sum // 0')
    balance=$((funded - spent))
    echo "Balance: $balance satoshis"
else
    echo "❌ Balance JSON is invalid"
fi
