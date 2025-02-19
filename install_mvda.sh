#!/bin/bash

uv pip install open3d numpy opencv-python

#install Video-Depth-Anything
git clone https://github.com/DepthAnything/Video-Depth-Anything
cd Video-Depth-Anything
uv pip install -r requirements.txt

mkdir checkpoints
cd checkpoints
# wget https://huggingface.co/depth-anything/Video-Depth-Anything-Large/resolve/main/video_depth_anything_vitl.pth
wget https://huggingface.co/depth-anything/Video-Depth-Anything-Small/resolve/main/video_depth_anything_vits.pth
cd ..


#install Depth-Anything-V2
git clone https://github.com/DepthAnything/Depth-Anything-V2
cd Depth-Anything-V2
uv pip install -r requirements.txt

cd metric_depth
mkdir checkpoints
cd checkpoints
# wget https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Hypersim-Large/resolve/main/depth_anything_v2_metric_hypersim_vitl.pth
wget https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Hypersim-Small/resolve/main/depth_anything_v2_metric_hypersim_vits.pth
cd ..

cd ..
cd ..
cd ..

cp -a src/metric_dpt_func.py Video-Depth-Anything/Depth-Anything-V2/metric_depth/.
cp -a src/video_metric_convert.py Video-Depth-Anything/.

