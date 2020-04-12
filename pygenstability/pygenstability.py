"""main functions"""
import multiprocessing
from collections import defaultdict
from tqdm import tqdm

import numpy as np
import scipy as sc
import networkx as nx

from sklearn.metrics.cluster import normalized_mutual_info_score

from generalizedLouvain_API import run_louvain, evaluate_quality
from .io import save
from .constructors import load_constructor


def _get_chunksize(n_comp, pool):
    """Split jobs accross workers for speedup."""
    return int(n_comp / pool._processes)  # pylint: disable=protected-access


def _graph_checks(graph):
    """Do some checks and preprocessing of the graph."""
    if not nx.is_connected(graph):
        print("Graph not connected, so we will use the largest connected component.")
        graph = nx.subgraph(graph, max(nx.connected_components(graph), key=len))

    if nx.is_directed(graph):
        print("Warning, your graph is directed!")
    return graph


def _get_times(params):
    """Get the time vectors from params."""
    if params["log_time"]:
        return np.logspace(params["min_time"], params["max_time"], params["n_time"])
    return np.linspace(params["min_time"], params["max_time"], params["n_time"])


def run(graph, params, constructor_custom=None, tqdm_disable=False):
    """Main funtion to compute clustering at various time scales."""
    graph = _graph_checks(graph)
    times = _get_times(params)

    constructor = load_constructor(
        params["constructor"], constructor_custom=constructor_custom
    )

    pool = multiprocessing.Pool(params["n_workers"])

    all_results = defaultdict(list)
    all_results["params"] = params
    for time in tqdm(times, disable=tqdm_disable):
        quality_matrix, null_model = constructor(graph, time)

        louvain_results = run_several_louvains(
            quality_matrix, null_model, params["n_runs"], pool
        )

        process_louvain_run(time, np.array(louvain_results), all_results)

        if params["compute_mutual_information"]:
            compute_mutual_information(
                louvain_results, all_results, pool, n_partitions=params["n_partitions"],
            )

        save(all_results)

    if params["compute_ttprime"]:
        compute_ttprime(all_results, pool)

    if params["apply_postprocessing"]:
        apply_postprocessing(all_results, pool, graph=graph, constructor=constructor)

    pool.close()

    return all_results


def process_louvain_run(time, louvain_results, all_results, mutual_information=None):
    """convert the louvain outputs to useful data and save it"""
    best_run_id = np.argmax(louvain_results[:, 0])
    all_results["times"].append(time)
    all_results["number_of_communities"].append(
        np.max(louvain_results[best_run_id, 1]) + 1
    )
    all_results["stability"].append(louvain_results[best_run_id, 0])
    all_results["community_id"].append(louvain_results[best_run_id, 1])

    if mutual_information is not None:
        all_results["mutual_information"].append(mutual_information)


def compute_mutual_information(louvain_results, all_results, pool, n_partitions=10):
    """compute the mutual information between the top partitions"""
    if len(louvain_results[0]) == 2:
        top_run_ids = np.argsort(louvain_results[:, 0])[-n_partitions:]
        top_partitions = louvain_results[top_run_ids, 1]
    else:  # if no stability provided, take first partitions
        top_partitions = louvain_results[:n_partitions]

    index_pairs = [[i, j] for i in range(n_partitions) for j in range(n_partitions)]

    worker = WorkerMI(top_partitions)

    chunksize = _get_chunksize(len(index_pairs), pool)
    all_results["mutual_information"].append(
        np.mean(list(pool.map(worker, index_pairs, chunksize=chunksize)))
    )


class WorkerMI:
    """worker for Louvain runs"""

    def __init__(self, top_partitions):
        self.top_partitions = top_partitions

    def __call__(self, index_pair):
        return normalized_mutual_info_score(
            self.top_partitions[index_pair[0]],
            self.top_partitions[index_pair[1]],
            average_method="arithmetic",
        )


def _to_indices(matrix):
    """convert a sparse matrix to indices and values"""
    rows, cols, values = sc.sparse.find(sc.sparse.tril(matrix))
    return (rows, cols), values


class WorkerLouvain:
    """worker for Louvain runs"""

    def __init__(self, quality_indices, quality_values, null_model):
        self.quality_indices = quality_indices
        self.quality_values = quality_values
        self.null_model = null_model

    def __call__(self, i):
        stability, community_id = run_louvain(
            self.quality_indices[0],
            self.quality_indices[1],
            self.quality_values,
            len(self.quality_values),
            self.null_model,
            np.shape(self.null_model)[0],
            1.0,
        )
        return stability, community_id


class WorkerQuality:
    """worker for Louvain runs"""

    def __init__(self, qualities_index, null_model):
        self.quality_indices = qualities_index[0]
        self.quality_values = qualities_index[1]
        self.null_model = null_model

    def __call__(self, partition_id):
        quality = evaluate_quality(
            self.quality_indices[0],
            self.quality_indices[1],
            self.quality_values,
            len(self.quality_values),
            self.null_model,
            np.shape(self.null_model)[0],
            1.0,
            partition_id,
        )
        return quality


def run_several_louvains(quality_matrix, null_model, n_runs, pool):
    """run several louvain on the current quality matrix"""

    quality_indices, quality_values = _to_indices(quality_matrix)
    worker = WorkerLouvain(quality_indices, quality_values, null_model)

    chunksize = _get_chunksize(n_runs, pool)
    return np.array(list(pool.map(worker, range(n_runs), chunksize=chunksize)))


def compute_ttprime(all_results, pool):
    """compute ttprime from the stability results"""
    index_pairs = [
        [i, j]
        for i in range(len(all_results["times"]))
        for j in range(len(all_results["times"]))
    ]

    worker = WorkerMI(all_results["community_id"])
    chunksize = _get_chunksize(len(index_pairs), pool)
    ttprime_list = list(pool.map(worker, index_pairs, chunksize=chunksize))

    all_results["ttprime"] = np.zeros(
        [len(all_results["times"]), len(all_results["times"])]
    )
    for i, ttp in enumerate(ttprime_list):
        all_results["ttprime"][index_pairs[i][0], index_pairs[i][1]] = ttp


def apply_postprocessing(all_results, pool, graph, constructor, tqdm_disable=False):
    """apply postprocessing"""

    all_results_raw = all_results.copy()

    for i, time in tqdm(
        enumerate(all_results["times"]),
        total=len(all_results["times"]),
        disable=tqdm_disable,
    ):
        quality_matrix, null_model = constructor(graph, time)

        worker = WorkerQuality(_to_indices(quality_matrix), null_model)
        best_quality_id = np.argmax(
            list(
                pool.map(
                    worker,
                    all_results_raw["community_id"],
                    chunksize=_get_chunksize(
                        len(all_results_raw["community_id"]), pool
                    ),
                )
            )
        )

        all_results["community_id"][i] = all_results_raw["community_id"][
            best_quality_id
        ]
        all_results["stability"][i] = all_results_raw["stability"][best_quality_id]
        all_results["number_of_communities"][i] = all_results_raw[
            "number_of_communities"
        ][best_quality_id]
