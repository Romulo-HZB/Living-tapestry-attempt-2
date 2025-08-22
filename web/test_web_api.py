#!/usr/bin/env python3
"""
Test script for the web interface
"""

import requests
import json

def test_web_api():
    """Test the web API endpoints"""
    base_url = "http://localhost:5000"
    
    # Test getting game state
    try:
        response = requests.get(f"{base_url}/api/state")
        if response.status_code == 200:
            print("✓ Game state endpoint working")
            state = response.json()
            print(f"  Game tick: {state.get('game_tick', 'N/A')}")
            print(f"  Player: {state.get('player', {}).get('name', 'N/A')}")
        else:
            print(f"✗ Game state endpoint failed with status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("✗ Could not connect to server. Is it running?")
        return
    except Exception as e:
        print(f"✗ Error testing game state endpoint: {e}")
    
    # Test getting locations
    try:
        response = requests.get(f"{base_url}/api/locations")
        if response.status_code == 200:
            print("✓ Locations endpoint working")
            locations = response.json()
            print(f"  Found {len(locations)} locations")
        else:
            print(f"✗ Locations endpoint failed with status {response.status_code}")
    except Exception as e:
        print(f"✗ Error testing locations endpoint: {e}")
    
    # Test getting actors
    try:
        response = requests.get(f"{base_url}/api/actors")
        if response.status_code == 200:
            print("✓ Actors endpoint working")
            actors = response.json()
            print(f"  Found {len(actors)} actors")
        else:
            print(f"✗ Actors endpoint failed with status {response.status_code}")
    except Exception as e:
        print(f"✗ Error testing actors endpoint: {e}")

if __name__ == "__main__":
    print("Testing Living Tapestry Web Interface...")
    print("=" * 40)
    test_web_api()
    print("=" * 40)
    print("Test complete. Make sure the server is running before running this test.")