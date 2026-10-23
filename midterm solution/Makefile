PYTHON ?= python3

.PHONY: test demo analyze benchmark mpi mcp
test:
	$(PYTHON) -m unittest discover -s tests -v
demo:
	$(PYTHON) -m midterm run examples/analysis_plan.json --no-history
analyze:
	$(PYTHON) -m midterm analyze examples/analysis_plan.json --no-history
benchmark:
	$(PYTHON) -m midterm benchmark examples/analysis_plan.json --no-history
mpi:
	mpiexec -n 5 $(PYTHON) -m midterm run examples/analysis_plan.json --backend mpi --no-history --output results/mpi.json
mcp:
	$(PYTHON) -m midterm run examples/mcp_plan.json --no-history --output results/mcp.json
