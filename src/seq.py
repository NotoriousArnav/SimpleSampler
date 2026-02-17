"""Dev entry point for the sequencer. Run with: uv run src/seq.py bank.json"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "simplesampler", ".."))

from simplesampler.sequencer.app import main

if __name__ == "__main__":
    main()
