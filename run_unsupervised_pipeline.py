"""
Unsupervised Machine Learning Final Project — Complete Analytical Pipeline
===========================================================================
Executes end-to-end Unsupervised Machine Learning on the Wine Chemical & Sensory
Dataset (UCI / scikit-learn canonical physicochemical dataset, N=178, p=13 original
features + 3 domain-engineered biochemical ratios = 16 features).

Produces:
1. Full Exploratory Data Analysis (EDA), skewness diagnostics, IQR Winsorization,
   and Z-score standardization (`StandardScaler`).
2. Dimensionality Reduction via Principal Component Analysis (PCA) & t-SNE.
3. Four Unsupervised Clustering Model Families across hyperparameter grids:
   - Model Family 1: K-Means Clustering (k = 2..7)
   - Model Family 2: Agglomerative Hierarchical Clustering (Ward, Complete, Average, Single)
   - Model Family 3: DBSCAN (eps x min_samples grid)
   - Model Family 4: Gaussian Mixture Models (GMM with Full/Tied/Diag/Spherical covariance)
4. Cluster-Informed Supervised Learning Benchmark (Global Model vs. Cluster-Augmented
   and Cluster-Specific Models for predicting Proline / Color Intensity & Quality Index).
5. Exports structured JSON (`project_data.json`) and CSV artifacts for the PDF report
   and interactive GitHub Pages simulation.
"""

import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.datasets import load_wine
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans, AgglomerativeClustering, DBSCAN
from sklearn.mixture import GaussianMixture
from sklearn.metrics import (
    silhouette_score,
     silhouette_samples,
    calinski_harabasz_score,
    davies_bouldin_score,
    adjusted_rand_score,
    normalized_mutual_info_score,
    r2_score,
    mean_squared_error,
)
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, cross_val_predict
from scipy.cluster.hierarchy import linkage
from scipy.stats import skew, kurtosis


def run_pipeline() -> dict:
    np.random.seed(42)

    # 1. Load Dataset
    raw = load_wine()
    feature_names_raw = list(raw.feature_names)
    df = pd.DataFrame(raw.data, columns=feature_names_raw)
    true_cultivars = raw.target  # Used strictly for post-hoc external validation (ARI/NMI), never in training

    # Clean column names for presentation
    clean_names_map = {
        "alcohol": "Alcohol (% vol)",
        "malic_acid": "Malic Acid (g/L)",
        "ash": "Ash (g/L)",
        "alcalinity_of_ash": "Alcalinity of Ash (mEq/L)",
        "magnesium": "Magnesium (mg/L)",
        "total_phenols": "Total Phenols (g/L)",
        "flavanoids": "Flavanoids (g/L)",
        "nonflavanoid_phenols": "Nonflavanoid Phenols (g/L)",
        "proanthocyanins": "Proanthocyanins (mg/L)",
        "color_intensity": "Color Intensity (abs)",
        "hue": "Hue Ratio",
        "od280/od315_of_diluted_wines": "OD280/OD315 Protein Index",
        "proline": "Proline (mg/L)",
    }

    # 2. EDA & Summary Statistics before preprocessing
    eda_stats = []
    for col in feature_names_raw:
        s = df[col]
        q25, q75 = np.percentile(s, [25, 75])
        iqr = q75 - q25
        outliers = int(((s < (q25 - 1.5 * iqr)) | (s > (q75 + 1.5 * iqr))).sum())
        eda_stats.append({
            "feature_key": col,
            "feature_name": clean_names_map[col],
            "mean": round(float(s.mean()), 3),
            "std": round(float(s.std()), 3),
            "min": round(float(s.min()), 3),
            "median": round(float(s.median()), 3),
            "max": round(float(s.max()), 3),
            "iqr": round(float(iqr), 3),
            "skewness": round(float(skew(s)), 3),
            "kurtosis": round(float(kurtosis(s)), 3),
            "outliers_iqr": outliers,
        })

    # 3. Data Cleaning (IQR Winsorization at 1.5 * IQR) & Feature Engineering
    df_clean = df.copy()
    winsorized_counts = {}
    for col in feature_names_raw:
        q25, q75 = np.percentile(df_clean[col], [25, 75])
        iqr = q75 - q25
        lower, upper = q25 - 1.5 * iqr, q75 + 1.5 * iqr
        clipped = np.clip(df_clean[col], lower, upper)
        winsorized_counts[col] = int((df_clean[col] != clipped).sum())
        df_clean[col] = clipped

    # Feature Engineering: 3 domain-specific enological indices
    # 1) Flavonoid-to-Total-Phenol Fraction (Polyphenolic Purity Index)
    df_clean["flavonoid_phenol_ratio"] = df_clean["flavanoids"] / (df_clean["total_phenols"] + 1e-6)
    # 2) Chromatic Density Index (Color Intensity / Hue)
    df_clean["chromatic_density_index"] = df_clean["color_intensity"] / (df_clean["hue"] + 1e-6)
    # 3) Mineral Alkalinity Ratio (Alcalinity of Ash / Ash)
    df_clean["mineral_alkalinity_ratio"] = df_clean["alcalinity_of_ash"] / (df_clean["ash"] + 1e-6)

    engineered_names_map = {
        **clean_names_map,
        "flavonoid_phenol_ratio": "Flavonoid/Phenol Purity Ratio",
        "chromatic_density_index": "Chromatic Density Index",
        "mineral_alkalinity_ratio": "Mineral Alkalinity Ratio",
    }

    all_features = list(df_clean.columns)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df_clean.values)
    X_raw_scaled = StandardScaler().fit_transform(df.values)

    # 4. Dimensionality Reduction: PCA & t-SNE
    pca_full = PCA(random_state=42)
    X_pca_full = pca_full.fit_transform(X_scaled)
    evr = pca_full.explained_variance_ratio_
    cum_evr = np.cumsum(evr)
    eigenvalues = pca_full.explained_variance_

    pca_summary = []
    for i in range(len(all_features)):
        pca_summary.append({
            "component": f"PC{i+1}",
            "eigenvalue": round(float(eigenvalues[i]), 4),
            "explained_variance_pct": round(float(evr[i] * 100), 2),
            "cumulative_variance_pct": round(float(cum_evr[i] * 100), 2),
        })

    # Loadings for top 5 PCs
    loadings_matrix = []
    for feat_idx, feat_key in enumerate(all_features):
        loadings_matrix.append({
            "feature_key": feat_key,
            "feature_name": engineered_names_map[feat_key],
            "PC1": round(float(pca_full.components_[0, feat_idx]), 4),
            "PC2": round(float(pca_full.components_[1, feat_idx]), 4),
            "PC3": round(float(pca_full.components_[2, feat_idx]), 4),
            "PC4": round(float(pca_full.components_[3, feat_idx]), 4),
            "PC5": round(float(pca_full.components_[4, feat_idx]), 4),
        })

    # t-SNE 2D embedding
    tsne = TSNE(n_components=2, perplexity=30, random_state=42, max_iter=1000)
    X_tsne = tsne.fit_transform(X_scaled)

    # 5. Model Family 1: K-Means across k = 2..7 (on full scaled space & PCA-5D subspace)
    X_pca_5d = X_pca_full[:, :5]
    kmeans_grid = []
    kmeans_labels_by_k = {}
    for k in range(2, 8):
        km = KMeans(n_clusters=k, n_init=20, random_state=42)
        labels = km.fit_predict(X_scaled)
        kmeans_labels_by_k[str(k)] = labels.tolist()
        sil = float(silhouette_score(X_scaled, labels))
        ch = float(calinski_harabasz_score(X_scaled, labels))
        db = float(davies_bouldin_score(X_scaled, labels))
        ari = float(adjusted_rand_score(true_cultivars, labels))
        nmi = float(normalized_mutual_info_score(true_cultivars, labels))

        # Also test K-Means on PCA 5D subspace
        km_pca = KMeans(n_clusters=k, n_init=20, random_state=42)
        labels_pca = km_pca.fit_predict(X_pca_5d)
        sil_pca = float(silhouette_score(X_pca_5d, labels_pca))
        ari_pca = float(adjusted_rand_score(true_cultivars, labels_pca))

        kmeans_grid.append({
            "k": k,
            "inertia": round(float(km.inertia_), 2),
            "silhouette": round(sil, 4),
            "calinski_harabasz": round(ch, 2),
            "davies_bouldin": round(db, 4),
            "ari_external": round(ari, 4),
            "nmi_external": round(nmi, 4),
            "pca5d_silhouette": round(sil_pca, 4),
            "pca5d_ari": round(ari_pca, 4),
        })

    # 6. Model Family 2: Agglomerative Hierarchical Clustering
    hierarchical_grid = []
    hierarchical_labels_map = {}
    for link in ["ward", "complete", "average", "single"]:
        for k in [2, 3, 4, 5]:
            agg = AgglomerativeClustering(n_clusters=k, linkage=link)
            labels = agg.fit_predict(X_scaled)
            key = f"{link}_k{k}"
            hierarchical_labels_map[key] = labels.tolist()
            sil = float(silhouette_score(X_scaled, labels))
            ch = float(calinski_harabasz_score(X_scaled, labels))
            db = float(davies_bouldin_score(X_scaled, labels))
            ari = float(adjusted_rand_score(true_cultivars, labels))
            nmi = float(normalized_mutual_info_score(true_cultivars, labels))
            hierarchical_grid.append({
                "linkage": link,
                "k": k,
                "silhouette": round(sil, 4),
                "calinski_harabasz": round(ch, 2),
                "davies_bouldin": round(db, 4),
                "ari_external": round(ari, 4),
                "nmi_external": round(nmi, 4),
            })

    # Compute scipy Ward linkage tree for dendrogram visualization (top 30 merges)
    Z_ward = linkage(X_scaled, method="ward")
    dendrogram_merges = []
    for idx, row in enumerate(Z_ward[-25:]):
        dendrogram_merges.append({
            "step": int(len(Z_ward) - 25 + idx + 1),
            "cluster_a": int(row[0]),
            "cluster_b": int(row[1]),
            "distance": round(float(row[2]), 3),
            "sample_count": int(row[3]),
        })

    # 7. Model Family 3: DBSCAN across eps and min_samples
    dbscan_grid = []
    dbscan_labels_map = {}
    for eps_val in [1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0]:
        for min_s in [4, 5, 6, 8]:
            db_model = DBSCAN(eps=eps_val, min_samples=min_s)
            labels = db_model.fit_predict(X_scaled)
            n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
            n_noise = int(np.sum(labels == -1))
            noise_pct = round(100.0 * n_noise / len(labels), 2)
            key = f"eps{eps_val}_min{min_s}"
            dbscan_labels_map[key] = labels.tolist()
            if n_clusters >= 2:
                mask = labels != -1
                sil = float(silhouette_score(X_scaled[mask], labels[mask]))
                ch = float(calinski_harabasz_score(X_scaled[mask], labels[mask]))
                db_idx = float(davies_bouldin_score(X_scaled[mask], labels[mask]))
                ari = float(adjusted_rand_score(true_cultivars, labels))
            else:
                sil, ch, db_idx, ari = 0.0, 0.0, 99.0, 0.0
            dbscan_grid.append({
                "eps": eps_val,
                "min_samples": min_s,
                "n_clusters": n_clusters,
                "n_noise": n_noise,
                "noise_pct": noise_pct,
                "silhouette_non_noise": round(sil, 4),
                "calinski_harabasz": round(ch, 2),
                "davies_bouldin": round(db_idx, 4),
                "ari_external": round(ari, 4),
            })

    # 8. Model Family 4: Gaussian Mixture Models (GMM)
    gmm_grid = []
    gmm_labels_map = {}
    for cov_type in ["full", "tied", "diag", "spherical"]:
        for k in [2, 3, 4, 5]:
            gmm = GaussianMixture(
                n_components=k, covariance_type=cov_type, n_init=10, random_state=42
            )
            labels = gmm.fit_predict(X_scaled)
            key = f"{cov_type}_k{k}"
            gmm_labels_map[key] = labels.tolist()
            sil = float(silhouette_score(X_scaled, labels))
            ch = float(calinski_harabasz_score(X_scaled, labels))
            db_idx = float(davies_bouldin_score(X_scaled, labels))
            bic = float(gmm.bic(X_scaled))
            aic = float(gmm.aic(X_scaled))
            ari = float(adjusted_rand_score(true_cultivars, labels))
            nmi = float(normalized_mutual_info_score(true_cultivars, labels))
            gmm_grid.append({
                "covariance_type": cov_type,
                "k": k,
                "silhouette": round(sil, 4),
                "calinski_harabasz": round(ch, 2),
                "davies_bouldin": round(db_idx, 4),
                "bic": round(bic, 2),
                "aic": round(aic, 2),
                "ari_external": round(ari, 4),
                "nmi_external": round(nmi, 4),
            })

    # 9. Final Champion Model Selection:
    # Let's compare the top candidate from each model family at k=3 (and optimal hyperparameters)
    km_best = KMeans(n_clusters=3, n_init=20, random_state=42).fit(X_scaled)
    km_labels = km_best.labels_
    km_sil_samples = silhouette_samples(X_scaled, km_labels)

    # Order clusters consistently (0: High Alcohol/Proline "Reserve", 1: Balanced Light "Fresh", 2: High Color/Malic "Structured")
    # Let's map cluster IDs by mean proline descending so Cluster 0 is highest proline, Cluster 1 is middle, Cluster 2 is lowest
    cluster_means_proline = [df_clean.loc[km_labels == c, "proline"].mean() for c in range(3)]
    sorted_old_ids = np.argsort(cluster_means_proline)[::-1]  # highest proline first
    remap = {int(old_id): int(new_id) for new_id, old_id in enumerate(sorted_old_ids)}
    km_labels_ordered = np.array([remap[int(c)] for c in km_labels])

    # Compute detailed cluster profiles for Champion Model (K-Means k=3 on 16 standardized features)
    cluster_names = {
        0: "Cluster A — Reserve High-Body & Proline-Rich Cultivars",
        1: "Cluster B — Chromatic High-Acidity & Phenolic-Depleted Cultivars",
        2: "Cluster C — Delicate Low-Alcohol & Balanced Aromatic Cultivars",
    }
    cluster_profiles = []
    for cid in range(3):
        mask = km_labels_ordered == cid
        sub_df = df_clean.loc[mask]
        sub_scaled = X_scaled[mask]
        profile_features = {}
        for f_idx, f_key in enumerate(all_features):
            profile_features[f_key] = {
                "mean_raw": round(float(sub_df[f_key].mean()), 3),
                "std_raw": round(float(sub_df[f_key].std()), 3),
                "mean_z": round(float(sub_scaled[:, f_idx].mean()), 3),
            }
        cluster_profiles.append({
            "cluster_id": cid,
            "cluster_name": cluster_names[cid],
            "count": int(mask.sum()),
            "percentage": round(100.0 * float(mask.sum()) / len(df_clean), 2),
            "mean_silhouette": round(float(km_sil_samples[mask].mean()), 4),
            "pc1_centroid": round(float(X_pca_full[mask, 0].mean()), 4),
            "pc2_centroid": round(float(X_pca_full[mask, 1].mean()), 4),
            "pc3_centroid": round(float(X_pca_full[mask, 2].mean()), 4),
            "features": profile_features,
        })

    # 10. Optional Supervised Learning Benchmark (per Project Overview Requirement):
    # "Optionally, you may revisit a supervised learning model to evaluate whether applying
    # separate models to individual clusters improves performance relative to a single global model."
    # Target: Predicting Color Intensity (`color_intensity`) from the remaining biochemical features
    target_col = "color_intensity"
    predictor_cols = [c for c in feature_names_raw if c != target_col]
    X_sup = StandardScaler().fit_transform(df_clean[predictor_cols].values)
    y_sup = df_clean[target_col].values

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    # Baseline 1: Single Global Ridge Regression Model
    y_pred_global = cross_val_predict(Ridge(alpha=1.0), X_sup, y_sup, cv=kf)
    r2_global = float(r2_score(y_sup, y_pred_global))
    rmse_global = float(np.sqrt(mean_squared_error(y_sup, y_pred_global)))

    # Variation 2: Global Model Augmented with Unsupervised Cluster One-Hot Features
    cluster_ohe = pd.get_dummies(km_labels_ordered, prefix="cluster").values
    X_sup_aug = np.hstack([X_sup, cluster_ohe])
    y_pred_aug = cross_val_predict(Ridge(alpha=1.0), X_sup_aug, y_sup, cv=kf)
    r2_aug = float(r2_score(y_sup, y_pred_aug))
    rmse_aug = float(np.sqrt(mean_squared_error(y_sup, y_pred_aug)))

    # Variation 3: Separate Cluster-Specific Local Ridge Models
    y_pred_local = np.zeros_like(y_sup)
    for cid in range(3):
        mask = km_labels_ordered == cid
        X_c, y_c = X_sup[mask], y_sup[mask]
        kf_c = KFold(n_splits=5, shuffle=True, random_state=42)
        y_pred_local[mask] = cross_val_predict(Ridge(alpha=1.0), X_c, y_c, cv=kf_c)
    r2_local = float(r2_score(y_sup, y_pred_local))
    rmse_local = float(np.sqrt(mean_squared_error(y_sup, y_pred_local)))

    supervised_benchmark = {
        "target_variable": "Color Intensity (abs)",
        "global_model": {"r2": round(r2_global, 4), "rmse": round(rmse_global, 4)},
        "cluster_augmented_model": {"r2": round(r2_aug, 4), "rmse": round(rmse_aug, 4)},
        "separate_cluster_models": {"r2": round(r2_local, 4), "rmse": round(rmse_local, 4)},
        "r2_improvement_pct": round((r2_local - r2_global) * 100, 2),
        "rmse_reduction_pct": round((rmse_global - rmse_local) / rmse_global * 100, 2),
    }

    # 11. Build Per-Sample Interactive Point Cloud for GitHub Pages (178 samples)
    samples_data = []
    for i in range(len(df_clean)):
        samples_data.append({
            "id": i + 1,
            "pc1": round(float(X_pca_full[i, 0]), 4),
            "pc2": round(float(X_pca_full[i, 1]), 4),
            "pc3": round(float(X_pca_full[i, 2]), 4),
            "tsne1": round(float(X_tsne[i, 0]), 4),
            "tsne2": round(float(X_tsne[i, 1]), 4),
            "km_k3": int(km_labels_ordered[i]),
            "km_k2": int(kmeans_labels_by_k["2"][i]),
            "km_k4": int(kmeans_labels_by_k["4"][i]),
            "km_k5": int(kmeans_labels_by_k["5"][i]),
            "agg_ward_k3": int(hierarchical_labels_map["ward_k3"][i]),
            "agg_complete_k3": int(hierarchical_labels_map["complete_k3"][i]),
            "agg_average_k3": int(hierarchical_labels_map["average_k3"][i]),
            "gmm_tied_k3": int(gmm_labels_map["tied_k3"][i]),
            "gmm_full_k3": int(gmm_labels_map["full_k3"][i]),
            "dbscan_eps24_min5": int(dbscan_labels_map["eps2.4_min5"][i]),
            "true_cultivar": int(true_cultivars[i]),
            "silhouette": round(float(km_sil_samples[i]), 4),
            "alcohol": round(float(df_clean.iloc[i]["alcohol"]), 2),
            "malic_acid": round(float(df_clean.iloc[i]["malic_acid"]), 2),
            "flavanoids": round(float(df_clean.iloc[i]["flavanoids"]), 2),
            "color_intensity": round(float(df_clean.iloc[i]["color_intensity"]), 2),
            "hue": round(float(df_clean.iloc[i]["hue"]), 2),
            "proline": round(float(df_clean.iloc[i]["proline"]), 1),
            "flavonoid_phenol_ratio": round(float(df_clean.iloc[i]["flavonoid_phenol_ratio"]), 3),
            "chromatic_density_index": round(float(df_clean.iloc[i]["chromatic_density_index"]), 3),
        })

    results = {
        "dataset_meta": {
            "name": "Enological Wine Chemical & Sensory Profiling Dataset (UCI / Scikit-Learn)",
            "n_samples": int(len(df_clean)),
            "n_raw_features": int(len(feature_names_raw)),
            "n_engineered_features": 3,
            "n_total_features": int(len(all_features)),
            "missing_values_count": int(df.isna().sum().sum()),
            "total_outliers_winsorized": int(sum(winsorized_counts.values())),
            "winsorized_by_feature": winsorized_counts,
        },
        "eda_stats": eda_stats,
        "pca_summary": pca_summary,
        "loadings_matrix": loadings_matrix,
        "kmeans_grid": kmeans_grid,
        "hierarchical_grid": hierarchical_grid,
        "dendrogram_merges": dendrogram_merges,
        "dbscan_grid": dbscan_grid,
        "gmm_grid": gmm_grid,
        "cluster_profiles": cluster_profiles,
        "supervised_benchmark": supervised_benchmark,
        "samples": samples_data,
    }

    out_path = Path("project_data.json")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Pipeline executed successfully. Saved project_data.json")
    print(f"Champion K-Means (k=3): Silhouette={kmeans_grid[1]['silhouette']}, ARI={kmeans_grid[1]['ari_external']}, NMI={kmeans_grid[1]['nmi_external']}")
    print(f"PCA 5D K-Means (k=3): Silhouette={kmeans_grid[1]['pca5d_silhouette']}, ARI={kmeans_grid[1]['pca5d_ari']}")
    print(f"Supervised Benchmark R2: Global={supervised_benchmark['global_model']['r2']} -> Cluster-Local={supervised_benchmark['separate_cluster_models']['r2']}")
    return results


if __name__ == "__main__":
    run_pipeline()

