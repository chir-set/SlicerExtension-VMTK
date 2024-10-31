import logging
import os
from typing import Annotated, Optional

import vtk
import  qt

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
from slicer import vtkMRMLSegmentationNode


#
# ArterialCalcificationPreProcessor
#

class ArterialCalcificationPreProcessor(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Arterial calcification pre-processor"
        self.parent.categories = ["Vascular Modeling Toolkit"]
        self.parent.dependencies = [] 
        self.parent.contributors = ["Saleem Edah-Tally [Surgeon] [Hobbyist developer]"]  # TODO: replace with "Firstname Lastname (Organization)"
        # TODO: update with short description of the module and a link to online module documentation
        self.parent.helpText = _("""
Segment calcifications around an arterial lumen within a margin.
See more information in <a href="href="https://github.com/vmtk/SlicerExtension-VMTK/">module documentation</a>.
""")
        # TODO: replace with organization, grant and thanks
        self.parent.acknowledgementText = _("""
This file was originally developed by Jean-Christophe Fillion-Robin, Kitware Inc., Andras Lasso, PerkLab,
and Steve Pieper, Isomics, Inc. and was partially funded by NIH grant 3P41RR013218-12S1.
""")

#
# ArterialCalcificationPreProcessorParameterNode
#

@parameterNodeWrapper
class ArterialCalcificationPreProcessorParameterNode:
    """
    The parameters needed by module.
    """
    inputVolume: slicer.vtkMRMLScalarVolumeNode
    marginSize: float = 4.0
    
    inputSegmentation: slicer.vtkMRMLSegmentationNode
    inputSegmentID: str = ""

#
# ArterialCalcificationPreProcessorWidget
#

class ArterialCalcificationPreProcessorWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
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
        self._show3DAction = None

    def setup(self) -> None:
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath('UI/ArterialCalcificationPreProcessor.ui'))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)
        
        self.workSpaceWidgets = [self.ui.inputsCollapsibleButton,
                            self.ui.applyButton]

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = ArterialCalcificationPreProcessorLogic()
        self.ui.parameterSetSelector.addAttribute("vtkMRMLScriptedModuleNode", "ModuleName", self.moduleName)

        # Connections
        self.ui.parameterSetSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.parameterSetChanged)
        self.ui.parameterSetSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.updateSliceViews)

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)
        
        # Application connections
        self.ui.inputSegmentSelector.connect("currentNodeChanged(vtkMRMLNode*)", self.onSegmentationChanged)
        self.ui.inputSegmentSelector.connect("currentSegmentChanged(QString)", self.onSegmentChanged)
        
        self.ui.applyButton.menu().clear()
        self._show3DAction = qt.QAction(_("Show 3D on success"))
        self._show3DAction.setCheckable(True)
        self._show3DAction.setChecked(True)
        self.ui.applyButton.menu().addAction(self._show3DAction)

        # Buttons
        self.ui.applyButton.connect('clicked(bool)', self.onApplyButton)

        # Let the parameter set be None; a study can thus be selected from a loaded saved scene.
        if self.ui.parameterSetSelector.nodeCount():
            wasBlocked = self.ui.parameterSetSelector.blockSignals(True)
            self.ui.parameterSetSelector.setCurrentNode(None)
            self.ui.parameterSetSelector.blockSignals(wasBlocked)
        
        # A parameter set must be selected to use the module.
        self.enableWorkSpace()

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

    def setParameterNode(self, inputParameterNode: Optional[ArterialCalcificationPreProcessorParameterNode]) -> None:
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
        self.logic.setParameterNode(self._parameterNode)
        self.enableWorkSpace()

    def _guardDog(self, caller=None, event=None):
        pass

    def parameterSetChanged(self, newParameterSet):
        # When all nodes are deleted, the wrapper outputs errors and alien nodes appear in the selector.
        if not newParameterSet:
            self.setParameterNode(None)
            return
        nextParameterNode = ArterialCalcificationPreProcessorParameterNode(newParameterSet)
        self.setParameterNode(nextParameterNode)
        self.restoreSegment()

    def updateSliceViews(self):
        parameterNode = self._parameterNode
        if (not parameterNode) or (parameterNode and not parameterNode.inputVolume):
            return
        slicer.util.setSliceViewerLayers(background = parameterNode.inputVolume.GetID(), fit = True)

    def onApplyButton(self) -> None:

        inputSegmentation = self.ui.inputSegmentSelector.currentNode()
        optionShow3D = self._show3DAction.checked
        
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            
            # Compute output
            self.logic.process()
        
        if (not optionShow3D) or (not inputSegmentation):
            return
        
        # Poked from qMRMLSegmentationShow3DButton.cxx
        if inputSegmentation.GetSegmentation().CreateRepresentation(slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName()):
            inputSegmentation.GetDisplayNode().SetPreferredDisplayRepresentationName3D(slicer.vtkSegmentationConverter.GetSegmentationClosedSurfaceRepresentationName())

    # Update the parameter node.
    def onSegmentationChanged(self, node):
        if not self._parameterNode:
            return
        if self._parameterNode.inputSegmentation == node:
            return
        self._parameterNode.inputSegmentation = node

    # Update the parameter node.
    def onSegmentChanged(self, segmentID):
        if not self._parameterNode:
            return
        if self._parameterNode.inputSegmentID == segmentID:
            return
        self._parameterNode.inputSegmentID = segmentID

    # Update the segmentation widgets from the parameter node.
    def restoreSegment(self):
        segmentSelector = self.ui.inputSegmentSelector
        wasBlocked = segmentSelector.blockSignals(True)
        segmentSelector.setCurrentNode(self._parameterNode.inputSegmentation)
        segmentSelector.setCurrentSegmentID(self._parameterNode.inputSegmentID)
        segmentSelector.blockSignals(wasBlocked)

    # Selecting a parameter set is an entry point.
    def enableWorkSpace(self):
        enabled = (self._parameterNode is not None)
        for widget in self.workSpaceWidgets:
            widget.setEnabled(enabled)

#
# ArterialCalcificationPreProcessorLogic
#

class ArterialCalcificationPreProcessorLogic(ScriptedLoadableModuleLogic):

    def __init__(self) -> None:
        """
        Called when the logic class is instantiated. Can be used for initializing member variables.
        """
        ScriptedLoadableModuleLogic.__init__(self)
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
            return ArterialCalcificationPreProcessorParameterNode(superScriptedModule)
        else:
            return ArterialCalcificationPreProcessorParameterNode(scriptedModule)

    def process(self) -> None:

        inputSegmentation = self._parameterNode.inputSegmentation
        inputVolume = self._parameterNode.inputVolume
        segmentID = self._parameterNode.inputSegmentID
        marginSize = self._parameterNode.marginSize

        if not inputSegmentation or not inputVolume or not segmentID or segmentID == "":
            raise ValueError(_("Input segmentation, volume or segment ID is invalid"))
        
        import time
        startTime = time.time()
        logging.info(_("Processing started"))
        
        # Ensure the segment is visible so that SegmentStatistics can process it.
        inputSegmentation.GetDisplayNode().SetSegmentVisibility(segmentID, True)
        
        """
        We need the volume to get intensity values.
        We don't set the segment editor's volume input. They are expected to be
        the same.
        """
        import SegmentStatistics
        ssLogic = SegmentStatistics.SegmentStatisticsLogic()
        ssLogic.getParameterNode().SetParameter("Segmentation", inputSegmentation.GetID())
        ssLogic.getParameterNode().SetParameter("ScalarVolume", inputVolume.GetID())
        ssLogic.computeStatistics()
        
        # k = ssLogic.getNonEmptyKeys()
        medianSegmentHU = float(ssLogic.getStatisticsValueAsString(segmentID, "ScalarVolumeSegmentStatisticsPlugin.median"))
        maxSegmentHU = float(ssLogic.getStatisticsValueAsString(segmentID, "ScalarVolumeSegmentStatisticsPlugin.max"))
        maxVolumeHU = inputVolume.GetImageData().GetScalarRange()[1]
        
        # Create segment editor object if needed.
        segmentEditorModuleWidget = slicer.util.getModuleWidget("SegmentEditor")
        seWidget = segmentEditorModuleWidget.editor
        seWidget.setSegmentationNode(inputSegmentation)
        seWidget.setSourceVolumeNode(inputVolume)
        inputSegmentation.SetReferenceImageGeometryParameterFromVolumeNode(inputVolume)
        seWidget.mrmlSegmentEditorNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        seWidget.mrmlSegmentEditorNode().SourceVolumeIntensityMaskOff()
        seWidget.mrmlSegmentEditorNode().SetOverwriteMode(seWidget.mrmlSegmentEditorNode().OverwriteNone)
        
        # Create calcification segment by duplicating the lumen.
        lumenSegmentName = inputSegmentation.GetSegmentation().GetSegment(segmentID).GetName()
        calcifSegmentID = segmentID + "_Dense_Calcification"
        calcifSegmentName = lumenSegmentName + "_Dense_Calcification"
        if inputSegmentation.GetSegmentation().GetSegment(calcifSegmentID):
            inputSegmentation.GetSegmentation().RemoveSegment(calcifSegmentID)
        calcifSegmentID = inputSegmentation.GetSegmentation().AddEmptySegment(calcifSegmentID)
        seWidget.mrmlSegmentEditorNode().SetSelectedSegmentID(calcifSegmentID)
        seWidget.setActiveEffectByName("Logical operators")
        effect = seWidget.activeEffect()
        effect.setParameter("BypassMasking", str(1))
        effect.setParameter("Operation", "COPY")
        effect.setParameter("ModifierSegmentID", segmentID)
        effect.self().onApply()
        seWidget.setActiveEffectByName(None)
        inputSegmentation.GetSegmentation().GetSegment(calcifSegmentID).SetName(calcifSegmentName)
        
        # Calculate and set calcification intensity range, a reasonable arbitrary range.
        calcifHURange = ((medianSegmentHU + maxSegmentHU) / 2.0, maxVolumeHU * 0.95 )
        seWidget.mrmlSegmentEditorNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedOutsideVisibleSegments)
        seWidget.mrmlSegmentEditorNode().SourceVolumeIntensityMaskOn()
        seWidget.mrmlSegmentEditorNode().SetSourceVolumeIntensityMaskRange(calcifHURange[0], calcifHURange[1])
        
        # Grow by margin within intensity range.
        seWidget.setActiveEffectByName("Margin")
        effect = seWidget.activeEffect()
        effect.setParameter("ApplyToAllVisibleSegments", str(0))
        effect.setParameter("MarginSizeMm", str(marginSize))
        effect.self().onApply()
        seWidget.setActiveEffectByName(None)
        
        seWidget.mrmlSegmentEditorNode().SetMaskMode(slicer.vtkMRMLSegmentationNode.EditAllowedEverywhere)
        seWidget.mrmlSegmentEditorNode().SourceVolumeIntensityMaskOff()
        
        # Subtract the lumen.
        seWidget.setActiveEffectByName("Logical operators")
        effect = seWidget.activeEffect()
        effect.setParameter("BypassMasking", str(1))
        effect.setParameter("Operation", "SUBTRACT")
        effect.setParameter("ModifierSegmentID", segmentID)
        effect.self().onApply()
        seWidget.setActiveEffectByName(None)
        
        seWidget.mrmlSegmentEditorNode().SetSelectedSegmentID(calcifSegmentID)
        inputSegmentation.GetDisplayNode().SetSegmentOpacity3D(segmentID, 0.5)
        inputSegmentation.GetDisplayNode().SetSegmentOpacity3D(calcifSegmentID, 0.5)

        stopTime = time.time()
        durationValue = '%.2f' % (stopTime-startTime)
        logging.info(_("Processing completed in {duration} seconds").format(duration=durationValue))

        return calcifSegmentID
#
# ArterialCalcificationPreProcessorTest
#

class ArterialCalcificationPreProcessorTest(ScriptedLoadableModuleTest):

    def setUp(self):
        """ Do whatever is needed to reset the state - typically a scene clear will be enough.
        """
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here.
        """
        self.setUp()
        self.test_ArterialCalcificationPreProcessor1()

    def test_ArterialCalcificationPreProcessor1(self):

        self.delayDisplay(_("Starting the test"))

        self.delayDisplay(_("Test passed"))
