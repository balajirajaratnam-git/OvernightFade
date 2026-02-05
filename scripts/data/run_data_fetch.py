"""
Wrapper script to run data_manager with ALLOW_NETWORK=1
"""
import os
import sys

# Set environment variable
os.environ['ALLOW_NETWORK'] = '1'

# Add src to path
sys.path.insert(0, 'src')

# Import and run data manager
from data_manager import DataManager

if __name__ == "__main__":
    try:
        dm = DataManager(require_network=True)
        dm.run()
    except RuntimeError as e:
        print(f"Network Error: {e}")
