"""The engine's tests.

A machine that uses the engine has settings of its own: which adapter fills
a slot, which model, where a server is. The tests are about the engine as it
comes, so those are put aside here, before any test is loaded. A test that
wants one sets it.
"""

import os

#: The settings a machine makes its own choices with: one variable a slot.
CHOICES = ("ADAPTER", "MODEL", "URL", "KEY", "EXTRA")

for _name in [name for name in os.environ
              if name.startswith(tuple(f"CONSTRUCTIVE_MEDIA_{choice}_" for choice in CHOICES))]:
    del os.environ[_name]
