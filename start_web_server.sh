#!/bin/bash
echo "Starting Living Tapestry Web Server..."
echo "Make sure you have installed the required dependencies with:"
echo "pip install -r requirements.txt"
echo ""
echo "Then open your browser and go to http://localhost:5000"
echo ""
cd "$(dirname "$0")"
python web/server.py