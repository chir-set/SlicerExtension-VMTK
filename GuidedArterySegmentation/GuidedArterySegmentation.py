import logging
import os
from typing import Annotated, Optional

import vtk, ctk, qt

import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)

from slicer import vtkMRMLScalarVolumeNode
from slicer import vtkMRMLSliceNode
from slicer import vtkMRMLMarkupsFiducialNode
from slicer import vtkMRMLMarkupsCurveNode
from slicer import vtkMRMLSegmentationNode
from slicer import vtkMRMLModelNode
#
# GuidedArterySegmentation
#

class GuidedArterySegmentation(ScriptedLoadableModule):
  """Uses ScriptedLoadableModule base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self, parent) -> None:
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "Guided artery segmentation"
    self.parent.categories = ["Vascular Modeling Toolkit"]
    self.parent.dependencies = ["ExtractCenterline"]
    self.parent.contributors = ["Saleem Edah-Tally [Surgeon] [Hobbyist developer]", "Andras Lasso (PerkLab)"]
    self.parent.helpText = _("""
This <a href="https://github.com/vmtk/SlicerExtension-VMTK/">module</a> is intended to create a segmentation from a contrast enhanced CT angioscan, and to finally extract centerlines from the surface model.
<br><br>It assumes that curve control points are placed in the contrasted lumen.
<br><br>The 'Flood filling' and 'Split volume' effects of the '<a href="https://github.com/lassoan/SlicerSegmentEditorExtraEffects">Segment editor extra effects</a>' are used.
<br><br>The '<a href="https://github.com/vmtk/SlicerExtension-VMTK/tree/master/ExtractCenterline/">SlicerExtension-VMTK Extract centerline</a>' module is required.
""")
    # TODO: replace with organization, grant and thanks
    self.parent.acknowledgementText = _("""
This file was originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab,
and Steve Pieper, Isomics, Inc. and was partially funded by NIH grant 3P41RR013218-12S1.
""")

#
# GuidedArterySegmentationParameterNode
#

@parameterNodeWrapper
class GuidedArterySegmentationParameterNode:
  # N.B. - we cannot reference the Shape node here since it is an additional markups.
  #      - either the module is not loaded or the parameter node complains (1).
  inputCurveNode: slicer.vtkMRMLMarkupsCurveNode
  inputSliceNode: slicer.vtkMRMLSliceNode
  tubeDiameter: float = 8.0
  intensityTolerance: int = 100
  neighbourhoodSize: float = 2.0
  optionExtractCenterlines: bool = False
  outputSegmentationNode: slicer.vtkMRMLSegmentationNode
  # These do not have widget counterparts.
  inputVolumeNode: slicer.vtkMRMLScalarVolumeNode
  outputFiducialNode: slicer.vtkMRMLMarkupsFiducialNode # 'Extract centerline' endpoints
  outputCenterlineModelNode: slicer.vtkMRMLModelNode
  outputCenterlineCurveNode: slicer.vtkMRMLMarkupsCurveNode

#
# GuidedArterySegmentationWidget
#

class GuidedArterySegmentationWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
  """Uses ScriptedLoadableModuleWidget base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self, parent=None) -> None:
    """
    Called when the user opens the module the first time and the widget is initialized.
    """
    ScriptedLoadableModuleWidget.__init__(self, parent)
    VTKObservationMixin.__init__(self)  # needed for parameter node observation
    self.logic = None
    self._parameterNode = None
    self._parameterNodeGuiTag = None
    # For the Shape node.
    self._updatingGUIFromParameterNode = False

  def setup(self) -> None:
    """
    Called when the user opens the module the first time and the widget is initialized.
    """
    ScriptedLoadableModuleWidget.setup(self)

    # Load widget from .ui file (created by Qt Designer).
    # Additional widgets can be instantiated manually and added to self.layout.
    uiWidget = slicer.util.loadUI(self.resourcePath('UI/GuidedArterySegmentation.ui'))
    self.layout.addWidget(uiWidget)
    self.ui = slicer.util.childWidgetVariables(uiWidget)
    
    self.workSpaceWidgets = [self.ui.inputsCollapsibleButton,
                        self.ui.extentCollapsibleGroupBox,
                        self.ui.floodFillingCollapsibleGroupBox,
                        self.ui.extractCenterlinesCheckBox,
                        self.ui.applyButton]

    # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
    # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
    # "setMRMLScene(vtkMRMLScene*)" slot.
    uiWidget.setMRMLScene(slicer.mrmlScene)

    # Create logic class. Logic implements all computations that should be possible to run
    # in batch mode, without a graphical user interface.
    self.logic = GuidedArterySegmentationLogic()
    self.ui.parameterSetSelector.addAttribute("vtkMRMLScriptedModuleNode", "ModuleName", self.moduleName)

    self.ui.floodFillingCollapsibleGroupBox.checked = False
    self.ui.extentCollapsibleGroupBox.checked = False

    # Connections
    self.ui.parameterSetSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.parameterSetChanged)
    self.ui.parameterSetSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.updateSliceViews)

    # These connections ensure that we update parameter node when scene is closed
    self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
    self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

    # Application connections
    self.ui.inputCurveSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onCurveNode)
    self.ui.inputShapeSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onShapeNode)
    self.ui.restoreSliceViewToolButton.connect("clicked()", self.onRestoreSliceViews)

    # Buttons
    self.ui.applyButton.connect('clicked(bool)', self.onApply)

    # A hidden one for the curious !
    shortcut = qt.QShortcut(self.ui.GuidedArterySegmentation)
    shortcut.setKey(qt.QKeySequence('Meta+d'))
    shortcut.connect( 'activated()', lambda: self.removeOutputNodes())

    self.updateGUIFromParameterNode()

    extensionName = "SegmentEditorExtraEffects"
    em = slicer.app.extensionsManagerModel()
    em.interactive = True
    restart = True
    if not em.installExtensionFromServer(extensionName, restart):
      raise ValueError(_("Failed to install {nameOfExtension} extension").format(nameOfExtension=extensionName))

    # Let the parameter set be None; a study can thus be selected from a loaded saved scene.
    if self.ui.parameterSetSelector.nodeCount():
      wasBlocked = self.ui.parameterSetSelector.blockSignals(True)
      self.ui.parameterSetSelector.setCurrentNode(None)
      self.ui.parameterSetSelector.blockSignals(wasBlocked)

    # A parameter set must be selected to use the module.
    self.enableWorkSpace()

  def inform(self, message) -> None:
    slicer.util.showStatusMessage(message, 3000)
    logging.info(message)

  def onCurveNode(self, node) -> None:
    if not node:
        return
    numberOfControlPoints = node.GetNumberOfControlPoints()
    if numberOfControlPoints < 3:
        self.inform(_("Curve node must have at least 3 points."))
        self._parameterNode.inputCurveNode = None

  def onShapeNode(self, node) -> None:
    if self._parameterNode:
      self._parameterNode.parameterNode.SetNodeReferenceID("inputShapeNode", None if not node else node.GetID())
    if node is None:
        self.ui.tubeDiameterSpinBoxLabel.setVisible(True)
        self.ui.tubeDiameterSpinBox.setVisible(True)
        self.logic.setShapeNode(node)
        return
    if node.GetShapeName() != slicer.vtkMRMLMarkupsShapeNode().Tube:
        self.inform(_("Shape node is not a Tube."))
        self.ui.tubeDiameterSpinBoxLabel.setVisible(True)
        self.ui.tubeDiameterSpinBox.setVisible(True)
        return
    numberOfControlPoints = node.GetNumberOfControlPoints()
    if numberOfControlPoints < 4:
        self.inform(_("Shape node must have at least 4 points."))
        self.ui.tubeDiameterSpinBoxLabel.setVisible(True)
        self.ui.tubeDiameterSpinBox.setVisible(True)
        return
    self.logic.setShapeNode(node)
    self.ui.tubeDiameterSpinBoxLabel.setVisible(False)
    self.ui.tubeDiameterSpinBox.setVisible(False)

  def updateSliceViews(self) -> None:
    if not self._parameterNode:
        return
    volumeNode = self._parameterNode.inputVolumeNode
    if not volumeNode:
      return
    sliceNode = self._parameterNode.inputSliceNode
    if not sliceNode:
        return
    # Don't upset UI if we have the right volume node
    sliceWidget = slicer.app.layoutManager().sliceWidget(sliceNode.GetName())
    backgroundVolumeNode = sliceWidget.sliceLogic().GetBackgroundLayer().GetVolumeNode()
    if volumeNode == backgroundVolumeNode:
        return
    slicer.util.setSliceViewerLayers(background = volumeNode.GetID(), fit = True)

  def onRestoreSliceViews(self) -> None:
    if self._parameterNode and self._parameterNode.inputVolumeNode:
      self.updateSliceViews()

  def cleanup(self) -> None:
    """
    Called when the application closes and the module widget is destroyed.
    """
    self.removeObservers()

  def enter(self) -> None:
    """Called each time the user opens this module."""
    # Make sure parameter node exists and observed
    if self._parameterNode:
      self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)

  def exit(self) -> None:
    """Called each time the user opens a different module."""
    # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
    if self._parameterNode:
      self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
      self._parameterNodeGuiTag = None

  def onSceneStartClose(self, caller, event) -> None:
    """Called just before the scene is closed."""

  def onSceneEndClose(self, caller, event) -> None:
    """Called just after the scene is closed."""
    self.parameterSetChanged(None)

  def setParameterNode(self, inputParameterNode: Optional[GuidedArterySegmentationParameterNode]) -> None:
      """
      Set and observe parameter node.
      Observation is needed to prevent a crash when working with multiple parameter sets.
      It may be removed if core is fixed.
      """

      if self._parameterNode:
          self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
          self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._guardDog)
      self._parameterNode = inputParameterNode
      if self._parameterNode:
          # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
          # ui element that needs connection.
          self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
          self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._guardDog)
          # For the Shape node.
          self.updateGUIFromParameterNode()
      self.logic.setParameterNode(self._parameterNode)
      self.enableWorkSpace()

  def _guardDog(self, caller=None, event=None):
    pass

  def parameterSetChanged(self, newParameterSet):
      # When all nodes are deleted, the wrapper outputs errors and alien nodes appear in the selector.
      if not newParameterSet:
          self.setParameterNode(None)
          return
      nextParameterNode = GuidedArterySegmentationParameterNode(newParameterSet)
      self.setParameterNode(nextParameterNode)

  def onApply(self) -> None:
    """
    Run processing when user clicks "Apply" button.
    """
    try:
      if not self.ui.parameterSetSelector.currentNode():
        self.inform((_("No parameter set defined.")))
        return
      if self._parameterNode.inputCurveNode is None:
        self.inform(_("No input curve node specified."))
        return
      if self._parameterNode.inputCurveNode.GetNumberOfControlPoints() < 3:
        self.inform(_("Input curve node must have at least 3 control points."))
        return
      if self._parameterNode.inputSliceNode is None:
        self.inform(_("No input slice node specified."))
        return
      # Ensure there's a background volume node.
      sliceNode = self._parameterNode.inputSliceNode
      sliceWidget = slicer.app.layoutManager().sliceWidget(sliceNode.GetName())
      volumeNode = sliceWidget.sliceLogic().GetBackgroundLayer().GetVolumeNode()
      if volumeNode is None:
        self.inform(_("No volume node selected in input slice node."))
        return
      self._parameterNode.inputVolumeNode = volumeNode

      # Compute output.
      self.logic.process()

      # Update segmentation selector if it was none.
      self.ui.outputSegmentationSelector.setCurrentNode(self._parameterNode.outputSegmentationNode)

    except Exception as e:
      slicer.util.errorDisplay(_("Failed to compute results: ") + str(e))
      import traceback
      traceback.print_exc()

  # Handy during development
  def removeOutputNodes(self) -> None:
    if not self._parameterNode:
        return
    # Remove segment, ID is controlled.
    segmentID = "Segment_" + self._parameterNode.inputCurveNode.GetID()
    segmentation = self._parameterNode.outputSegmentationNode
    segment = segmentation.GetSegmentation().GetSegment(segmentID)
    if segment:
        segmentation.GetSegmentation().RemoveSegment(segment)
    # Remove child centerline curves of self.outputCenterlineCurve
    shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
    outputCurveMainId = shNode.GetItemByDataNode(self._parameterNode.outputCenterlineCurveNode)
    if (outputCurveMainId > 0) and (outputCurveMainId != shNode.GetSceneItemID()):
        while shNode.GetNumberOfItemChildren(outputCurveMainId):
            outputCurveChildId = shNode.GetItemByPositionUnderParent(outputCurveMainId, 0)
            outputCurveChild = shNode.GetItemDataNode(outputCurveChildId)
            slicer.mrmlScene.RemoveNode(outputCurveChild)

    slicer.mrmlScene.RemoveNode(self._parameterNode.outputFiducialNode)
    slicer.mrmlScene.RemoveNode(self._parameterNode.outputCenterlineModelNode)
    slicer.mrmlScene.RemoveNode(self._parameterNode.outputCenterlineCurveNode)
    self._parameterNode.outputFiducialNode = None
    self._parameterNode.outputCenterlineModelNode = None
    self._parameterNode.outputCenterlineCurveNode = None

  def updateGUIFromParameterNode(self, caller=None, event=None):
    if self._updatingGUIFromParameterNode or (not self._parameterNode):
      return

    self._updatingGUIFromParameterNode = True

    # The shape node is not managed by the parameter node wrapper.
    inputShapeNode = self._parameterNode.parameterNode.GetNodeReference("inputShapeNode")
    self.logic.setShapeNode(inputShapeNode)
    self.ui.inputShapeSelector.setCurrentNode(inputShapeNode)
    self.ui.extentCollapsibleGroupBox.checked = (inputShapeNode is not None)

    self._updatingGUIFromParameterNode = False

   # Selecting a parameter set is an entry point.
  def enableWorkSpace(self):
    enabled = (self._parameterNode is not None)
    for widget in self.workSpaceWidgets:
      widget.setEnabled(enabled)

#
# GuidedArterySegmentationLogic
#

class GuidedArterySegmentationLogic(ScriptedLoadableModuleLogic):
  """This class should implement all the actual
  computation done by your module.  The interface
  should be such that other python code can import
  this class and make use of the functionality without
  requiring an instance of the Widget.
  Uses ScriptedLoadableModuleLogic base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def __init__(self) -> None:
    """
    Called when the logic class is instantiated. Can be used for initializing member variables.
    """
    ScriptedLoadableModuleLogic.__init__(self)
    self._shapeNode = None
    self._parameterNode = None

  def setParameterNode(self, parameterNode):
    self._parameterNode = parameterNode

  def getParameterNode(self):
    return self._parameterNode

  # This is a facility for scripting.
  def generateParameterNode(self, scriptedModule: slicer.vtkMRMLScriptedModuleNode = None):
    if not scriptedModule:
      superScriptedModule = super().getParameterNode()
      # This one must not appear in the parameter set selector.
      superScriptedModule.SetAttribute("ModuleName", "Scripted" + self.moduleName)
      return GuidedArterySegmentationParameterNode(superScriptedModule)
    else:
      return GuidedArterySegmentationParameterNode(scriptedModule)

  def setShapeNode(self, shapeNode) -> None:
    if shapeNode == self._shapeNode:
      return
    self._shapeNode = shapeNode

  def getShapeNode(self):
    return self._shapeNode

  def showStatusMessage(self, messages) -> None:
    separator = " "
    msg = separator.join(messages)
    slicer.util.showStatusMessage(msg, 3000)
    slicer.app.processEvents()

  def process(self) -> None:
    import time
    startTime = time.time()
    logging.info((_("Processing started")))

    slicer.util.showStatusMessage(_("Segment editor setup"))
    slicer.app.processEvents()

    # Create a new segmentation if none is specified.
    if not self._parameterNode.outputSegmentationNode:
        segmentation=slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
        self._parameterNode.outputSegmentationNode = segmentation
    else:
        # Prefer a local reference for readability
        segmentation = self._parameterNode.outputSegmentationNode

    # Create segment editor object if needed.
    segmentEditorModuleWidget = slicer.util.getModuleWidget("SegmentEditor")
    seWidget = segmentEditorModuleWidget.editor

    # Get slice widget and volume node.
    sliceWidget = slicer.app.layoutManager().sliceWidget(self._parameterNode.inputSliceNode.GetName())
    volumeNode = self._parameterNode.inputVolumeNode

    # Set segment editor controls
    seWidget.setSegmentationNode(segmentation)
    seWidget.setSourceVolumeNode(volumeNode)
    segmentation.SetReferenceImageGeometryParameterFromVolumeNode(volumeNode)

    # Show the input curve. Colour of control points change on selection, helps to wait.
    self._parameterNode.inputCurveNode.SetDisplayVisibility(True)
    # Reset segment editor masking widgets. Values set by previous work must not interfere here.
    seWidget.mrmlSegmentEditorNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
    seWidget.mrmlSegmentEditorNode().SourceVolumeIntensityMaskOff()
    seWidget.mrmlSegmentEditorNode().SetOverwriteMode(seWidget.mrmlSegmentEditorNode().OverwriteNone)

    if self._shapeNode is None:
      #---------------------- Draw tube with VTK---------------------
      # https://discourse.slicer.org/t/converting-markupscurve-to-markupsfiducial/20246/3
      tube = vtk.vtkTubeFilter()
      tube.SetInputData(self._parameterNode.inputCurveNode.GetCurveWorld())
      tube.SetRadius(self._parameterNode.tubeDiameter / 2)
      tube.SetNumberOfSides(30)
      tube.CappingOn()
      tube.Update()
      tubeMaskSegmentId = segmentation.AddSegmentFromClosedSurfaceRepresentation(tube.GetOutput(), "TubeMask")
    else:
      #---------------------- Draw tube from Shape node ---------------------
      tubeMaskSegmentId = segmentation.AddSegmentFromClosedSurfaceRepresentation(self._shapeNode.GetCappedTubeWorld(), "TubeMask")
    # Select it so that Split Volume can work on this specific segment only.
    seWidget.setCurrentSegmentID(tubeMaskSegmentId)

    #---------------------- Split volume ---------------------
    slicer.util.showStatusMessage("Split volume")
    slicer.app.processEvents()
    intensityRange = volumeNode.GetImageData().GetScalarRange()
    seWidget.setActiveEffectByName("Split volume")
    svEffect = seWidget.activeEffect()
    svEffect.setParameter("FillValue", intensityRange[0])
    # Work on the TubeMask segment only.
    svEffect.setParameter("ApplyToAllVisibleSegments", 0)
    svEffect.self().onApply()
    seWidget.setActiveEffectByName(None)

    # Get output split volume
    allScalarVolumeNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode")
    outputSplitVolumeNode = allScalarVolumeNodes.GetItemAsObject(allScalarVolumeNodes.GetNumberOfItems() - 1)
    # Remove no longer needed drawn tube segment
    segment = segmentation.GetSegmentation().GetSegment(tubeMaskSegmentId)
    segmentation.GetSegmentation().RemoveSegment(segment)
    # Replace master volume of segmentation
    seWidget.setSourceVolumeNode(outputSplitVolumeNode)
    segmentation.SetReferenceImageGeometryParameterFromVolumeNode(outputSplitVolumeNode)

    """
    Split Volume creates a folder that contains the segmentation node,
    and the split volume(s) it creates.
    Here, we need to get rid of the split volume. There is no reason to keep
    around the created folder, that takes owneship of the segmentation node.
    So we'll later move the segmentation node to the Scene node and remove the
    residual empty folder.
    """
    shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
    shSplitVolumeId = shNode.GetItemByDataNode(outputSplitVolumeNode)
    shSplitVolumeParentId = shNode.GetItemParent(shSplitVolumeId)
    shSegmentationId = shNode.GetItemByDataNode(segmentation)
    shSceneId = shNode.GetSceneItemID()

    #---------------------- Manage segment --------------------
    # Remove a segment node and keep its color
    segment = None
    segmentColor = []
    """
    Control the segment ID.
    It will be the same in all segmentations.
    We can reach it precisely.
    """
    segmentID = "Segment_" + self._parameterNode.inputCurveNode.GetID()
    segment = segmentation.GetSegmentation().GetSegment(segmentID)
    if segment:
        segmentColor = segment.GetColor()
        segmentation.GetSegmentation().RemoveSegment(segment)

    # Add a new segment, with controlled ID and known color.
    object = segmentation.GetSegmentation().AddEmptySegment(segmentID)
    segment = segmentation.GetSegmentation().GetSegment(object)
    # Visually identify the segment by the input fiducial name
    segmentName = "Segment_" + self._parameterNode.inputCurveNode.GetName()
    segment.SetName(segmentName)
    if len(segmentColor):
        segment.SetColor(segmentColor)
    # Select new segment
    seWidget.setCurrentSegmentID(segmentID)

    #---------------------- Flood filling ---------------------
    # Set parameters
    seWidget.setActiveEffectByName("Flood filling")
    ffEffect = seWidget.activeEffect()
    ffEffect.setParameter("IntensityTolerance", self._parameterNode.intensityTolerance)
    ffEffect.setParameter("NeighborhoodSizeMm", self._parameterNode.neighbourhoodSize)
    # +++ If an alien ROI is set, segmentation may fail and take an infinite time.
    ffEffect.parameterSetNode().SetNodeReferenceID("FloodFilling.ROI", None)
    ffEffect.updateGUIFromMRML()

    # Get input curve control points
    curveControlPoints = vtk.vtkPoints()
    self._parameterNode.inputCurveNode.GetControlPointPositionsWorld(curveControlPoints)
    numberOfCurveControlPoints = curveControlPoints.GetNumberOfPoints()

    # Apply flood filling at curve control points. Ignore first and last point as the resulting segment would be a big lump. The voxels of split volume at -1000 would be included in the segment.
    for i in range(1, numberOfCurveControlPoints - 1):
        # Show progress in status bar. Helpful to wait.
        t = time.time()
        durationValue = '%.2f' % (t-startTime)
        msg = _("Flood filling: {duration} seconds - ").format(duration=durationValue)
        self.showStatusMessage((msg, str(i + 1), "/", str(numberOfCurveControlPoints)))

        rasPoint = curveControlPoints.GetPoint(i)
        slicer.vtkMRMLSliceNode.JumpSlice(sliceWidget.sliceLogic().GetSliceNode(), *rasPoint)
        point3D = qt.QVector3D(rasPoint[0], rasPoint[1], rasPoint[2])
        point2D = ffEffect.rasToXy(point3D, sliceWidget)
        qIjkPoint = ffEffect.xyToIjk(point2D, sliceWidget, ffEffect.self().getClippedSourceImageData())
        ffEffect.self().floodFillFromPoint((int(qIjkPoint.x()), int(qIjkPoint.y()), int(qIjkPoint.z())))

    # Switch off active effect
    seWidget.setActiveEffect(None)
    # Replace master volume of segmentation
    seWidget.setSourceVolumeNode(volumeNode)
    segmentation.SetReferenceImageGeometryParameterFromVolumeNode(volumeNode)
    # Remove no longer needed split volume.
    slicer.mrmlScene.RemoveNode(outputSplitVolumeNode)

    # Remove folder created by Split Volume.
    # First, reparent the segmentation item to scene item.
    shNode.SetItemParent(shSegmentationId, shSceneId)
    """
    Remove an empty folder directly. Keep it if there are volumes from other
    work.
    """
    if shNode.GetNumberOfItemChildren(shSplitVolumeParentId) == 0:
        if shNode.GetItemLevel(shSplitVolumeParentId) == "Folder":
            shNode.RemoveItem(shSplitVolumeParentId)

    # Show segment. Poked from qMRMLSegmentationShow3DButton.cxx
    if segmentation.GetSegmentation().CreateRepresentation(slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName()):
        segmentation.GetDisplayNode().SetPreferredDisplayRepresentationName3D(slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName())

    if not self._parameterNode.optionExtractCenterlines:
        stopTime = time.time()
        durationValue = '%.2f' % (stopTime-startTime)
        message = _("Processing completed in {duration} seconds").format(duration=durationValue)
        logging.info(message)
        slicer.util.showStatusMessage(message, 5000)
        return segmentID

    #---------------------- Extract centerlines ---------------------
    slicer.util.showStatusMessage(_("Extract centerline setup"))
    slicer.app.processEvents()
    ecWidget = slicer.util.getModuleWidget('ExtractCenterline')
    ecUi = ecWidget.ui

    inputSurfaceComboBox = ecUi.inputSurfaceSelector
    inputSegmentSelectorWidget = ecUi.inputSegmentSelectorWidget
    endPointsMarkupsSelector = ecUi.endPointsMarkupsSelector
    outputCenterlineModelSelector = ecUi.outputCenterlineModelSelector
    outputCenterlineCurveSelector = ecUi.outputCenterlineCurveSelector
    preprocessInputSurfaceModelCheckBox = ecUi.preprocessInputSurfaceModelCheckBox
    applyButton = ecUi.applyButton
    outputNetworkGroupBox = ecUi.CollapsibleGroupBox

    # Set input segmentation
    inputSurfaceComboBox.setCurrentNode(segmentation)
    inputSegmentSelectorWidget.setCurrentSegmentID(segmentID)
    # Create 2 fiducial endpoints, at start and end of input curve. We call it output because it is not user input.
    outputFiducialNode = self._parameterNode.outputFiducialNode
    if not outputFiducialNode:
        # Use the basename of the combobox.
        outputFiducialNode = endPointsMarkupsSelector.addNode("vtkMRMLMarkupsFiducialNode")
        firstInputCurveControlPoint = self._parameterNode.inputCurveNode.GetNthControlPointPositionVector(0)
        outputFiducialNode.AddControlPointWorld(firstInputCurveControlPoint)
        lastInputCurveControlPoint = self._parameterNode.inputCurveNode.GetNthControlPointPositionVector(curveControlPoints.GetNumberOfPoints() - 1)
        outputFiducialNode.AddControlPointWorld(lastInputCurveControlPoint)
        self._parameterNode.outputFiducialNode = outputFiducialNode
    endPointsMarkupsSelector.setCurrentNode(outputFiducialNode)

    # Output centerline model. A single node throughout.
    centerlineModel = self._parameterNode.outputCenterlineModelNode
    if not centerlineModel:
        centerlineModel = outputCenterlineModelSelector.addNode("vtkMRMLModelNode")
        self._parameterNode.outputCenterlineModelNode = centerlineModel
    outputCenterlineModelSelector.setCurrentNode(centerlineModel)

    # Output centerline curve. A single node throughout.
    centerlineCurve = self._parameterNode.outputCenterlineCurveNode
    if not centerlineCurve:
        centerlineCurve = outputCenterlineCurveSelector.addNode("vtkMRMLMarkupsCurveNode")
        self._parameterNode.outputCenterlineCurveNode = centerlineCurve
    outputCenterlineCurveSelector.setCurrentNode(centerlineCurve)

    """
    Don't preprocess input surface. Decimation error may crash Slicer. Quadric method for decimation is slower but more reliable.
    """
    preprocessInputSurfaceModelCheckBox.setChecked(False)
    # Apply
    applyButton.click()
    # Hide the input curve to show the centerlines
    self._parameterNode.inputCurveNode.SetDisplayVisibility(False)
    # Close network pane; we don't use this here.
    outputNetworkGroupBox.collapsed = True

    slicer.util.mainWindow().moduleSelector().selectModule('ExtractCenterline')

    stopTime = time.time()
    durationValue = '%.2f' % (stopTime-startTime)
    message = _("Processing completed in {duration} seconds").format(duration=durationValue)
    logging.info(message)
    slicer.util.showStatusMessage(message, 5000)
    return segmentID

#
# GuidedArterySegmentationTest
#

class GuidedArterySegmentationTest(ScriptedLoadableModuleTest):
  """
  This is the test case for your scripted module.
  Uses ScriptedLoadableModuleTest base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def setUp(self) -> None:
    """ Do whatever is needed to reset the state - typically a scene clear will be enough.
    """
    slicer.mrmlScene.Clear()

  def runTest(self) -> None:
    """Run as few or as many tests as needed here.
    """
    self.setUp()
    self.test_GuidedArterySegmentation1()

  def test_GuidedArterySegmentation1(self) -> None:
    self.delayDisplay(_("Starting the test"))

    self.delayDisplay(_("Test passed"))
