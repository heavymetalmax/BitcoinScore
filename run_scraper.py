#!/usr/bin/env python3
"""Run the scraper module as a script without `python scraper/scraper.py` import issues.

Usage:
  python run_scraper.py
or
  ./run_scraper.py
"""
from scraper.scraper import main


if __name__ == '__main__':
    main()
