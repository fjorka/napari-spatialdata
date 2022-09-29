from __future__ import annotations
from typing import TypeVar, Any, TYPE_CHECKING, Optional, Union
import napari
from loguru import logger
from napari_spatialdata._utils import save_fig, NDArrayA
from napari_spatialdata._view import QtAdataViewWidget
from anndata import AnnData
import numpy as np
import itertools
from napari_spatialdata._constants._pkg_constants import Key
from skimage.measure import regionprops
import pandas as pd

# # cannot import these because of cyclic dependencies with spatialdata
# SpatialData = TypeVar("SpatialData")
# BaseElement = TypeVar("BaseElement")

if TYPE_CHECKING:
    from spatialdata import SpatialData
    from spatialdata._core.elements import BaseElement, Image, Labels, Points, Polygons

import matplotlib.pyplot as plt

__all__ = ["Interactive"]


class Interactive:
    """
    Interactive viewer for spatial data.

    Parameters
    ----------
    %(img_container)s
    %(_interactive.parameters)s
    """

    def __init__(self, sdata: SpatialData, with_widgets: bool = True):
        self._viewer = napari.Viewer()
        self._add_layers_from_sdata(sdata=sdata)
        # self._adata_view = QtAdataViewWidget(viewer=self._viewer)
        if with_widgets:
            self.show_widget()
        napari.run()

    def show_widget(self):
        """Load the widget for interative features exploration."""
        from napari.plugins import plugin_manager, _npe2

        plugin_manager.discover_widgets()
        _npe2.get_widget_contribution("napari-spatialdata")
        self._viewer.window.add_plugin_dock_widget("napari-spatialdata")

    def add_spatial_element(
        self, element: BaseElement, name: Optional[str] = None, annotation_table: Optional[AnnData] = None
    ) -> None:
        from spatialdata._core.elements import Image, Labels, Points, Polygons

        if isinstance(element, Image):
            # ignoring the annotation table
            self._add_image(element, name=name)
        elif isinstance(element, Labels):
            self._add_labels(element, annotation_table=annotation_table, name=name)
        elif isinstance(element, Points):
            self._add_points(element, annotation_table=annotation_table, name=name)
        elif isinstance(element, Polygons):
            self._add_polygons(element, annotation_table=annotation_table, name=name)
        else:
            raise ValueError(f"Unsupported element type: {type(element)}")

    def _add_image(self, image: Image, name: str = None) -> None:
        # TODO: add logic which takes into account for axes labels ([y, x, c] vs [c, y, x] vs [y, x], etc)
        # dropping c channel
        dims = image.data.dims
        rgb = False
        if len(dims) == 3:  # [c, y, x]
            new_image = image.data.transpose(dims[1], dims[0], dims[2])
            if new_image.shape[2] in [3, 4]:
                rgb = True
            scale = image.transforms.scale_factors[1:]
            translate = image.transforms.translation[1:]
        elif len(dims) == 2:  # [y, x]
            new_image = image.data.transpose(dims[1], dims[0])
            scale = image.transforms.scale_factors
            translate = image.transforms.translation
        else:
            raise ValueError(f"Unsupported image dimensions: {dims}")
        self._viewer.add_image(new_image, rgb=rgb, name=name, scale=scale, translate=translate)
        print("TODO: correct transform")

    def _add_labels(self, labels: Labels, name: str = None, annotation_table: Optional[AnnData] = None) -> None:
        annotation = self._find_annotation_for_regions(
            base_element=labels, name=name, annotation_table=annotation_table
        )
        if annotation is not None:
            instance_key = annotation_table.uns["mapping_info"]["instance_key"]
            metadata = {
                "adata": annotation,
                "library_id": name,
                "labels_key": instance_key,
                # "points": points1,
                # "point_diameter": 10,
            }
        else:
            metadata = None
        self._viewer.add_labels(labels.data.transpose(), name=name, metadata=metadata)

    def _add_points(self, points: Points, name: str, annotation_table: Optional[AnnData] = None) -> None:
        adata = points.data
        spatial = adata.obsm["spatial"]
        if "region_radius" in adata.obsm:
            radii = adata.obsm["region_radius"]
        else:
            radii = 1
        annotation = self._find_annotation_for_regions(
            base_element=points, annotation_table=annotation_table, name=name
        )
        if annotation is not None:
            # # points_annotation is required from the squidpy legagy code, TODO: remove
            # points_annotation = AnnData(X=points.data.X)
            # points_annotation.obs['gene'] = annotation.obs
            # metadata = {"adata": annotation, "library_id": name, "points": points_annotation}
            metadata = {"adata": annotation, "library_id": name}
        else:
            metadata = None
        self._viewer.add_points(
            spatial,
            name=name,
            edge_color="white",
            face_color="white",
            size=2 * radii,
            metadata=metadata,
            edge_width=0.0,
        )
        # img1, rgb=True, name="image1", metadata={"adata": adata, "library_id": "V1_Adult_Mouse_Brain"}, scale=(1, 1)

    def _add_polygons(self, polygons: Polygons, name: str, annotation_table: Optional[AnnData] = None) -> None:
        adata = polygons.data
        spatial = adata.obs.spatial
        from spatialdata._core import Polygons

        coordinates = [Polygons.string_to_tensor(s).tolist() for s in spatial]
        annotation = self._find_annotation_for_regions(
            base_element=polygons, annotation_table=annotation_table, name=name
        )
        if annotation is not None:
            metadata = {'adta': annotation, "library_id": name}
        else:
            metadata = None
        ##
        self._viewer.add_shapes(
            coordinates,
            shape_type="polygon",
            name=name,
            edge_width=5.,
            edge_color="white",
            face_color=np.array([0.0, 0, 0.0, 0.0]),
            metadata=metadata
        )
        ##

    def _find_annotation_for_regions(
        self, base_element: Union[Labels, Points, Polygons], name: str, annotation_table: Optional[AnnData] = None
    ) -> Optional[AnnData]:
        if annotation_table is None:
            return None
        from spatialdata._core.elements import Labels, Points, Polygons

        regions, regions_key, instance_key = self._get_mapping_info(annotation_table)
        if name in regions:
            annotating_rows = annotation_table[annotation_table.obs[regions_key] == name, :]
            if len(annotating_rows) == 0:
                logger.warning(f"Layer {name} expected to be annotated but no annotation found")
                return None
            else:
                if isinstance(base_element, Labels):
                    return self._find_annotation_for_labels(
                        labels=base_element, name=name, annotating_rows=annotating_rows, instance_key=instance_key
                    )
                elif isinstance(base_element, Points):
                    return self._find_annotation_for_points(
                        points=base_element, name=name, annotating_rows=annotating_rows, instance_key=instance_key
                    )
                elif isinstance(base_element, Polygons):
                    return self._find_annotation_for_polygons(
                        polygons=base_element, name=name, annotating_rows=annotating_rows, instance_key=instance_key
                    )
                else:
                    raise ValueError(f"Unsupported element type: {type(base_element)}")

        else:
            return None

        if annotation_table is None:
            return None

    def _get_mapping_info(self, annotation_table: AnnData):
        regions = annotation_table.uns["mapping_info"]["regions"]
        regions_key = annotation_table.uns["mapping_info"]["regions_key"]
        instance_key = annotation_table.uns["mapping_info"]["instance_key"]
        return regions, regions_key, instance_key

    def _find_annotation_for_labels(self, labels: Labels, name: str, annotating_rows: AnnData, instance_key: str):
        # TODO: use xarray apis
        x = np.array(labels.data)
        u = np.unique(x)
        backgrond = 0 in u
        # adjacent_labels = (len(u) - 1 if backgrond else len(u)) == np.max(u)
        available_u = annotating_rows.obs[instance_key]
        u_not_annotated = np.setdiff1d(u, available_u)
        if len(u_not_annotated) > 0:
            logger.warning(f"{len(u_not_annotated)}/{len(u)} labels not annotated: {u_not_annotated}")
            # TODO: display them in a different way, maybe in red
        annotating_rows = annotating_rows[annotating_rows.obs[instance_key].isin(u), :]

        # TODO: requirement due to the squidpy legacy code, in the future this will not be needed
        annotating_rows.uns[Key.uns.spatial] = {}
        annotating_rows.uns[Key.uns.spatial][name] = {}
        annotating_rows.uns[Key.uns.spatial][name][Key.uns.scalefactor_key] = {}
        annotating_rows.uns[Key.uns.spatial][name][Key.uns.scalefactor_key]["tissue_hires_scalef"] = 1.0
        # TODO: we need to flip the y axis here, investigate the reason of this mismatch
        # a user reported a similar behavior https://github.com/kevinyamauchi/ome-ngff-tables-prototype/pull/8#issuecomment-1165363992
        list_of_cx = []
        list_of_cy = []
        list_of_v = []
        regions = regionprops(x)
        for props in regions:
            cx, cy = props.centroid
            v = props.label
            list_of_cx.append(cx)
            list_of_cy.append(cy)
            list_of_v.append(v)
        centroids = pd.DataFrame({"cx": list_of_cx, "cy": list_of_cy, "v": list_of_v})
        merged = pd.merge(
            annotating_rows.obs, centroids, left_on=instance_key, right_on="v", how="left", indicator=True
        )
        background = merged.query('_merge == "left_only"')
        assert len(background) == 1
        assert background.loc[background.index[0], instance_key] == 0
        index_of_background = merged[merged[instance_key] == 0].index[0]
        merged.loc[index_of_background, "v"] = 0
        merged["v"] = merged["v"].astype(int)

        assert len(annotating_rows) == len(merged)
        assert annotating_rows.obs[instance_key].tolist() == merged["v"].tolist()

        merged_centroids = merged[["cx", "cy"]].to_numpy()
        assert len(merged_centroids) == len(merged)
        annotating_rows.obsm["spatial"] = np.fliplr(merged_centroids)
        annotating_rows.obsm["region_radius"] = np.array([10.0] * len(merged_centroids))  # arbitrary value
        return annotating_rows

    def _find_annotation_for_points(
        self, points: Points, name: str, annotating_rows: AnnData, instance_key: str
    ) -> Optional[AnnData]:
        """Find the annotation for a points layer from the annotation table."""
        assert instance_key in points.data.obs.columns
        available_instances = points.data.obs[instance_key].tolist()
        annotated_instances = annotating_rows.obs[instance_key].tolist()
        assert len(available_instances) == len(set(available_instances)), (
            "Instance keys must be unique. Found " "multiple regions instances with the " "same key."
        )
        assert len(annotated_instances) == len(set(annotated_instances)), (
            "Instance keys must be unique. Found " "multiple regions instances annotations with the same key."
        )
        available_instances = set(available_instances)
        annotated_instances = set(annotated_instances)
        assert annotated_instances.issubset(available_instances), "Annotation table contains instances not in points."
        if len(annotated_instances) != len(available_instances):
            raise ValueError("TODO: support partial annotation")

        # TODO: requirement due to the squidpy legacy code, in the future this will not be needed
        annotating_rows.uns[Key.uns.spatial] = {}
        annotating_rows.uns[Key.uns.spatial][name] = {}
        annotating_rows.uns[Key.uns.spatial][name][Key.uns.scalefactor_key] = {}
        annotating_rows.uns[Key.uns.spatial][name][Key.uns.scalefactor_key]["tissue_hires_scalef"] = 1.0
        # TODO: we need to flip the y axis here, investigate the reason of this mismatch
        # a user reported a similar behavior https://github.com/kevinyamauchi/ome-ngff-tables-prototype/pull/8#issuecomment-1165363992
        annotating_rows.obsm["spatial"] = np.fliplr(points.data.obsm["spatial"])
        # workaround for the legacy code to support different sizes for different points
        annotating_rows.obsm["region_radius"] = points.data.obsm["region_radius"]
        return annotating_rows

    def _find_annotation_for_polygons(self, polygons: Polygons, name: str, annotating_rows: AnnData, instance_key:
    str) -> Optional[AnnData]:
        print('_find_annotation_for_polygons not implemented')
        return None

    def _add_layers_from_sdata(self, sdata: SpatialData):
        ##
        merged = itertools.chain.from_iterable(
            (sdata.images.items(), sdata.labels.items(), sdata.points.items(), sdata.polygons.items())
        )
        for name, element in merged:
            self.add_spatial_element(element, annotation_table=sdata.table, name=name)
        ##

    def screenshot(
        self,
        return_result: bool = False,
        dpi: float | None = 180,
        save: str | None = None,
        canvas_only: bool = True,
        **kwargs: Any,
    ) -> NDArrayA | None:
        """
        Plot a screenshot of the viewer's canvas.

        Parameters
        ----------
        return_result
            If `True`, return the image as an :class:`numpy.uint8`.
        dpi
            Dots per inch.
        save
            Whether to save the plot.
        canvas_only
            Whether to show only the canvas or also the widgets.
        kwargs
            Keyword arguments for :meth:`matplotlib.axes.Axes.imshow`.

        Returns
        -------
        Nothing, if ``return_result = False``, otherwise the image array.
        """
        try:
            arr = np.asarray(self._viewer.screenshot(path=None, canvas_only=canvas_only))
        except RuntimeError as e:
            logger.error(f"Unable to take a screenshot. Reason: {e}")
            return None

        fig, ax = plt.subplots(nrows=1, ncols=1, dpi=dpi)
        fig.tight_layout()

        ax.imshow(arr, **kwargs)
        plt.axis("off")

        if save is not None:
            save_fig(fig, save)

        return arr if return_result else None

    def close(self) -> None:
        """Close the viewer."""
        self._viewer.close()

    # @property
    # def adata(self) -> AnnData:
    #     """Annotated data object."""
    #     # return self._controller._view.model.adata

    # def __repr__(self) -> str:
    # return f"Interactive view of {repr(self._controller.model.container)}"

    # def __str__(self) -> str:
    #     return repr(self)


if __name__ == "__main__":
    from spatialdata import SpatialData

    sdata = SpatialData.read("spatialdata-sandbox/merfish/data.zarr")
    Interactive(sdata)
