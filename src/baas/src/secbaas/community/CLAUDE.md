# secbaas Module Conventions

## Python Import Rules

1. **Never import a private module (`_module`) from another package.** Private modules are internal to their package — only the `__init__.py` is the public face. For example:
   - ❌ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` — in `api/__init__.py`
   - ✅ `from secbaas.core.service.api_gateway import APIKeyRecord` — imports via the public `__init__.py`

2. **Within the same package, use relative imports to refer to sibling private modules.** For example:
   - ✅ `from ._model import APIKeyRecord` — inside `api/api_gateway/`
   - ✅ `from ._repository import APIKeyRepository` — inside `core/service/api_gateway/`
   - ✅ `from ._protocols import APIKeyService` — inside `api/api_gateway/`

3. **Cross-package imports between private modules are allowed** when the importing file is itself a private module (not a public `__init__.py`). This is an internal implementation detail. For example:
   - ✅ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` inside `api/api_gateway/_protocols.py` — OK because `_protocols.py` is private
   - ✅ `from secbaas.core.service.api_gateway._repository import APIKeyRecord` inside `api/api_gateway/_model.py` — OK because `_model.py` is private

4. **Public `__init__.py` files must only import from public package paths** or from their own package's private modules via relative imports.