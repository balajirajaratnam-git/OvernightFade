import os
import json

def create_directory_structure():
    """
    Creates the project directory structure defined in Requirement 2.2.
    """
    # Root directories
    directories = [
        "data",
        "data/SPY",           # Default ticker storage
        "data/SPY/intraday",  # Minute-bar storage
        "logs",               # Requirement 12.5
        "config",             # Requirement 10.2
        "src",                # Source code modules
    ]

    print("--- 1. Creating Directory Structure ---")
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f" [OK] Created: {directory}/")
        except OSError as e:
            print(f" [Error] Failed to create {directory}: {e}")

def create_requirements_file():
    """
    Creates requirements.txt based on Requirement 12.2.
    """
    content = """# Core Data & Analysis
pandas>=2.0.0
numpy>=1.24.0
pyarrow>=12.0.0  # For Parquet file handling (Req 2.1)
pytz>=2023.3     # Timezone handling (Req 12.3)

# API & Networking
requests>=2.31.0
python-dotenv>=1.0.0 # Secure API key management

# User Interface
rich>=13.4.0     # Terminal dashboard (Req 7.8)
"""
    print("\n--- 2. Creating requirements.txt ---")
    with open("requirements.txt", "w") as f:
        f.write(content)
    print(" [OK] Created: requirements.txt")

def create_env_file():
    """
    Creates a template .env file for API Key management (Req 10.3).
    """
    content = """# Polygon.io API Key
# Paste your key after the equals sign. Do not use quotes.
POLYGON_API_KEY=PASTE_YOUR_KEY_HERE
"""
    print("\n--- 3. Creating .env template ---")
    # check if file exists to prevent overwriting an actual key
    if not os.path.exists(".env"):
        with open(".env", "w") as f:
            f.write(content)
        print(" [OK] Created: .env")
    else:
        print(" [Skip] .env already exists. Skipped to prevent overwriting your key.")

def create_config_file():
    """
    Creates the default configuration JSON (Req 10.2).
    """
    config = {
        "ticker": "SPY",
        "lookback_years": 2,
        "premium_budget": 100,
        "default_take_profit_atr": 0.5,
        "rate_limit_seconds": 13,  # Free tier throttle
        "filters": {
            "exclude_fridays": True,
            "exclude_flat_days": True,
            "enable_fade_green": True,
            "enable_fade_red": True
        },
        "directories": {
            "data": "data",
            "logs": "logs"
        }
    }
    
    print("\n--- 4. Creating Default Config ---")
    with open("config/config.json", "w") as f:
        json.dump(config, f, indent=4)
    print(" [OK] Created: config/config.json")

def create_init_files():
    """Makes src a proper Python package"""
    with open("src/__init__.py", "w") as f:
        pass

def main():
    print("Initializing Overnight Fade Decision Support System...")
    print("Reference: Requirements Document v2.1\n")
    
    create_directory_structure()
    create_requirements_file()
    create_env_file()
    create_config_file()
    create_init_files()
    
    print("\n" + "="*50)
    print("PROJECT SETUP COMPLETE")
    print("="*50)
    print("Next Steps:")
    print("1. Open '.env' and paste your Polygon.io API Key.")
    print("2. Install dependencies: pip install -r requirements.txt")
    print("3. You are ready for Phase 3: Data Infrastructure Implementation.")

if __name__ == "__main__":
    main()