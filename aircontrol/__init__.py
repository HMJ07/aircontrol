import warnings

__version__ = "0.1.1"

# MediaPipe llama a una función obsoleta de protobuf en cada fotograma y la consola se llenaba de avisos idénticos.
warnings.filterwarnings("ignore", message=r"SymbolDatabase\.GetPrototype", category=UserWarning)
