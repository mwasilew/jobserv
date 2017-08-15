from importlib import import_module

from jobserv.settings import STORAGE_BACKEND


Storage = import_module(STORAGE_BACKEND).Storage
