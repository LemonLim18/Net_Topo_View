declare module 'react-cytoscapejs' {
    import cytoscape from 'cytoscape';
    import { Component } from 'react';

    export interface CytoscapeComponentProps {
        id?: string;
        cy?: (cy: cytoscape.Core) => void;
        style?: React.CSSProperties;
        elements: cytoscape.ElementDefinition[];
        layout?: cytoscape.LayoutOptions;
        stylesheet?: cytoscape.Stylesheet[];
        className?: string;
        zoom?: number;
        pan?: { x: number; y: number };
        minZoom?: number;
        maxZoom?: number;
        zoomingEnabled?: boolean;
        userZoomingEnabled?: boolean;
        panningEnabled?: boolean;
        userPanningEnabled?: boolean;
        boxSelectionEnabled?: boolean;
        autoungrabify?: boolean;
        autounselectify?: boolean;
        autolock?: boolean;
        wheelSensitivity?: number;
    }

    export default class CytoscapeComponent extends Component<CytoscapeComponentProps> {
        static normalizeElements(elements: any): cytoscape.ElementDefinition[];
    }
}
