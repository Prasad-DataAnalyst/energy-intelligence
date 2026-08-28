"""
interfaces/__init__.py — Engine Infrastructure Base Classes
============================================================
Exports the three foundational abstract base classes used by every
module, job, and API manager in the YouTube AI Engine.

Usage
-----
    from interfaces import BaseModule, BaseJob, BaseManager
"""

from interfaces.base_module import BaseModule
from interfaces.base_job import BaseJob
from interfaces.base_manager import BaseManager

__all__ = ["BaseModule", "BaseJob", "BaseManager"]
