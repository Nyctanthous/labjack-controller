<h1 align="center">labjack-controller</h1>
<p style="text-align:center"><img src=https://labjack.com/sites/default/files/styles/slideshow/public/T7-Pro_engineering_sshow.jpg?itok=82O0k1DV/></p>

## An Easy Python Wrapper for LJM to Just Take Data Already

This is a package designed to make streaming data from [LabJack](https://labjack.com/) T-series devices easy.
## Device Compatibility

+ T7 and T7 Pro
+ T4

## Requirements

+ Python 3.5+
+ [LJM](https://labjack.com/support/software/installers/ljm)

All other requirements will be automatically acquired by `pip`; see `setup.py` for a complete list of all requirements that will be automatically obtained.

## Installation
[![Build Status](https://travis-ci.com/university-of-southern-maine-physics/labjack-controller.svg?branch=master)](https://travis-ci.com/university-of-southern-maine-physics/labjack-controller)

To install, clone this repository and install with `pip`. By terminal, this is

```bash
git clone https://github.com/university-of-southern-maine-physics/labjack-controller.git
cd labjack-controller
pip install -r requirements.txt
```

Alternatively, if you have [Python for LJM](https://labjack.com/support/software/installers/ljm) installed already, you can change the `pip` install command to `pip install .`

## Sample Usage

Multiple demonstrations of library functions are located in the `demos` folder. However, getting started with full streaming is as easy as

```python
from labjackcontroller.labtools import LabjackReader

myLabjack = LabjackReader("T7", connection_type="ETHERNET")

duration = 60  # Seconds
scan_rate = 50000  # Hz
max_channel_voltages = [10.0, 10.0]  # Volts
channels = ["AIN0", "AIN1"]

# Collect data from the above channels for 60 seconds.
myLabjack.collect_data(channels, max_channel_voltages, duration, scan_rate)

# Get all data recorded as a 2D Numpy array
my_data = myLabjack.to_list()
```

## Contributors

+ [Ben Montgomery](https://github.com/Nyctanthous)

## Special Thanks

+ [Paul Nakroshis](https://github.com/paulnakroshis)
