# Retired Backend worker image

Use `apps/evolution/Dockerfile` (repository-root build context). Worker entrypoints
and strategy dependencies belong to the independent Evolution package. The old
image copied Backend code but omitted canonical Evolution and shared contracts;
it is no longer a supported build recipe. No image was built or deployed here.
