## Installation
```python
pip install -r requirements.txt
```

## Tests
```python
# Run all tests
python test_telescope.py
python test_dome.py
python test_slaving.py

# List available tests
python test_telescope.py --list
python test_dome.py --list
python test_slaving.py --list

# Run specific tests
python tests/ascom_requests/test_telescope.py --test connect read_status tracking
python test_dome.py --test connect slew_azimuth abort_slew
python test_slaving.py --test multi_position

# Simulation mode (from anywhere, against simulated server)
python test_telescope.py --simulate
python test_dome.py --simulate
python test_slaving.py --simulate
```

## Scheduler
```python
# Generate an example schedule file
python scheduler.py --example > my_schedule.json

# Validate without running
python scheduler.py my_schedule.json --dry-run

# Execute for real
python scheduler.py my_schedule.json

# With file logging
python scheduler.py my_schedule.json --log-dir logs/schedules

# List all available actions
python scheduler.py --list-actions
```

## Scheduler testing
```python
# 1. Telescope only — connect, slew to 3 targets, park
python scheduler.py schedules/test_telescope.json --log-dir logs

# 2. Dome only — home, open shutter, slew to 90°/270°/0°, close, park
python scheduler.py schedules/test_dome.json --log-dir logs

# 3. Slaved — both devices, dome follows telescope through 3 slews
python scheduler.py schedules/test_slaving.json --log-dir logs

# Add --dry-run to any of them to preview without executing:
python scheduler.py schedules/test_telescope.json --dry-run

```
What each tests covers:
* telescope: Connect, unpark, enable tracking, slew to Orion (RA 5.3h), slew to Antares region (RA 16.7h), slew to Az 180° Alt 45°, park, disconnect
* Dome: Connect, find home, open shutter, slew east (90°), slew west (270°), slew north (0°), close shutter, park, disconnect
* Connect both, unpark telescope, home dome, open shutter, enable slaving, slew through 3 RA/Dec targets — dome should follow each time — then tear down both


## From main directory
```python
# from the Telescope-Backend/ directory
python -m tests.ascom_requests.test_telescope
python -m tests.ascom_requests.test_dome
python -m ascom_requests.scheduler ascom_requests/schedules/test_telescope.json --dry-run
```