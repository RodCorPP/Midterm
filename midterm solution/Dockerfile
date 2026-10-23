FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY midterm ./midterm
COPY examples ./examples
RUN python -m pip install --no-cache-dir '.[all]' mpich
CMD ["mpiexec", "-n", "5", "python", "-m", "midterm", "run", "examples/analysis_plan.json", "--backend", "mpi", "--no-history", "--output", "results/mpi.json"]
