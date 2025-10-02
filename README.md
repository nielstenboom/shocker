Run python webserver port 8000:

```
python3 -m http.server 8000
python3 -m http.server 8000 > /dev/null 2>&1 &

```

## debugging

```bash
# Reset all iptables counters to zero for clean testing
sudo iptables -t nat -Z
sudo iptables -Z

# Show current state (should be all zeros)
echo "=== BEFORE CURL - NAT TABLE ==="
sudo iptables -t nat -L -v -n --line-numbers

echo "=== BEFORE CURL - FORWARD CHAIN ==="
sudo iptables -L FORWARD -v -n --line-numbers

curl localhost:8000

# Check which rules were hit
echo "=== AFTER CURL - NAT TABLE ==="
sudo iptables -t nat -L -v -n --line-numbers

echo "=== AFTER CURL - FORWARD CHAIN ==="
sudo iptables -L FORWARD -v -n --line-numbers


curl 69.69.0.2:8000

echo "=== AFTER DIRECT CURL - NAT TABLE ==="
sudo iptables -t nat -L -v -n --line-numbers

echo "=== AFTER DIRECT CURL - FORWARD CHAIN ==="
sudo iptables -L FORWARD -v -n --line-numbers

```