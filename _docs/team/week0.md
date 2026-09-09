1) We added a test job and a build job
    - the test job tests first installs uv and then confirms that your tests are properly linted, formatted, and unit tests pass with ruff (on the VM)
    - the build job builds the dockerfile with Dockerfile and then runs the container such that it exectures a python commadn
2) we added to CI checks one for test and one for build
    - we added them each to the rulset and now it is required that they both pass for any PR to get merged 
    - the CI runner will never show cached like it does on your local machine because it is spinning up a new VM each time to build and run the docker container
3) Dockerfile
    - the uv syncs are separated because dependencies are installed from the lockfile before the source is copied, so a change to src/ invalidates only the layers below the copy and the dependency install stays cached. Then the second uv sync only installs vetter itself. Name the mechanism: layer caching, top down invalidation.