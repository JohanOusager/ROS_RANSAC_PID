#!/usr/bin/env python3
import numpy as np
from sklearn import linear_model
import rospy
import cv2 as cv
from sensor_msgs.msg import LaserScan
from dist_ransac.msg import Polar_dist

from time import time

MIN_RANGE = 0.4             #Meters
MAX_RANGE = 5.6             #Meters
RATE = 50                   #Hz
MIN_INLIERS = 10             #Observations
RESIDUAL_THRESHOLD = 0.1    #Meters
MAX_FAILS = 1               #Nr of times RANSAC may fail before we give up
MAX_CLUSTER_DIST = 0.25     #Meters, distance between points in distinct clusters

class RANSAC_subscriber():
    def __init__(self):
        rospy.init_node("ransac_wall_dist_pub", anonymous=True)
        topic = '/scan' # "/laser/scan" # for simulation
        self.subscription = rospy.Subscriber(topic, LaserScan, self.RANSAC)
        print('starting RANSAC node')
        self.publisher = rospy.Publisher("laser/dist_to_wall", Polar_dist, queue_size=1)
        self.rate = RATE
        self.max_range = MAX_RANGE
        self.min_range = MIN_RANGE
        self.min_inliers = MIN_INLIERS
        self.residual_threshold = RESIDUAL_THRESHOLD
        self.max_fails = MAX_FAILS
        self.max_cluster_dist = MAX_CLUSTER_DIST
        rospy.Rate(self.rate)
        self.image = np.array([0])
        self.drawScale = 125
        self.num = 0

    def RANSAC(self, msg):
        start_time = time()
        angle_min = msg.angle_min
        angle_max = msg.angle_max
        angle_inc = msg.angle_increment
        ranges = np.array(msg.ranges)

        if len(ranges) == 0:
            raise(IOError, "NO POINTS FROM SCANNER")
        angle_arr = np.arange(angle_min, angle_max+(0.1*angle_inc), angle_inc)

        def y_dist(angle, dist):
            #handle small value error
            #if np.isclose(np.sin(angle), 0) or np.isclose(dist, 0):
            #    return 0
            #else:
                #do trigonometry
                return np.sin(angle)*dist

        def x_dist(angle, dist):
            #handle small value error
            #if np.isclose(np.cos(angle), 0) or np.isclose(dist, 0):
            #    return 0
            #else:
                #do trignometry
                return np.cos(angle)*dist


        print("Get positions took: ", time()-start_time)
        start_time = time()
        positions = np.array([np.array([x_dist(a, d), y_dist(a, d)]) for (a, d) in zip(angle_arr, ranges)])
        print("trignometry took: ", time()-start_time)
        start_time = time()
        positions = positions[np.linalg.norm(positions, axis=1) < self.max_range]
        positions = positions[np.linalg.norm(positions, axis=1) > self.min_range] # Sort wheel points away

        if len(positions) == 0:
            raise(IOError, "NO IN-RANGE POINTS")

        print("Get positions took: ", time()-start_time)
        start_time = time()

        #noise
        def add_noise(points, n):
            return np.concatenate((points, (np.random.uniform(low=-msg.range_max, high=msg.range_max, size=(n,2)))))
        #positions = add_noise(positions, 25)

        self.image = np.zeros([np.int(np.ceil(self.drawScale*2*msg.range_max)),
                               np.int(np.ceil(self.drawScale*2*msg.range_max)), 3], dtype=np.uint8)
        self.draw_points(positions)

        print("Draw points took: ", time()-start_time)
        start_time = time()

        #split the dataset into clusters naively
        clusters = []
        cluster_start = 0
        for i in range(positions.shape[0]):
            if i == positions.shape[0] - 1:
                clusters.append(positions[cluster_start:])
                break
            if np.linalg.norm(np.absolute(positions[i]) - np.absolute(positions[i+1])) > self.max_cluster_dist:
                clusters.append(positions[cluster_start:i])
                cluster_start = i
        if clusters == []:
            clusters = np.array([positions])
        else:
            #clusters = np.array([positions])
            clusters = np.array(clusters)

        print("Clusters took: ", time()-start_time)
        start_time = time()

        # do a ransac
        fit_sets = []
        fit_models = []
        for points in clusters:
            while np.array(points).shape[0] > self.min_inliers:
                fails = 0
                try:
                    rs = linear_model.RANSACRegressor(min_samples=self.min_inliers,
                                                      residual_threshold=self.residual_threshold,
                                                      max_trials=10)
                    rs.fit(np.expand_dims(points[:, 0], axis=1), points[:, 1])
                    inlier_mask = rs.inlier_mask_
                    inlier_points = points[np.array(inlier_mask)]
                    min_x = np.min(inlier_points[:,0], axis=0)
                    max_x = np.max(inlier_points[:,0], axis=0)
                    start = np.array([min_x, rs.predict([[min_x]])[0]])
                    end = np.array([max_x, rs.predict([[max_x]])[0]])
                    fit_sets.append(inlier_points)
                    fit_models.append(np.array([start, end]))
                    points = points[~np.array(inlier_mask)]
                except:
                    fails += 1
                    if fails >= self.max_fails:
                       break

        print("RANSAC took: ", time()-start_time)
        start_time = time()

        self.draw_lines(fit_models, fit_sets)


        print("Line draw took: ", time()-start_time)
        start_time = time()

        def nearest_point_on_line(line_start, line_end, point=np.array((0,0))):
            line_start -= point
            line_end -= point
            a_to_p = -line_start
            a_to_b = line_end - line_start
            sq_mag_a_to_b = a_to_b[0]**2 + a_to_b[1]**2
            if sq_mag_a_to_b == 0:
            #    print("LINE OF ZERO LENGTH")
                return np.array([0, 0])
            dot_product = a_to_p[0]*a_to_b[0] + a_to_p[1]*a_to_b[1]
            dist_a_to_c = dot_product / sq_mag_a_to_b
            c = np.array([start[0] + a_to_b[0]*dist_a_to_c, start[1] + a_to_b[1]*dist_a_to_c])
            return c + point

        def is_point_between(s, e, p):
            if (np.linalg.norm(e-p) > np.linalg.norm(e-s) or np.linalg.norm(p-s) > np.linalg.norm(e-s)):
                return False
            else:
                return True

        min_dist = np.inf
        min_dist_point = np.array([0, 0])
        for model in fit_models:
            #find nearest point on the line, relative to the robot
            point = nearest_point_on_line(model[0], model[1])

            #get the distance to the point
            dist = np.sqrt(point[0]**2 + point[1]**2)

            #check if the point is on the line segment
            if not is_point_between(model[0], model[-1], point):
                dist_start = np.sqrt(model[0,0]**2 + model[0,1]**2)
                dist_end = np.sqrt(model[1,0]**2 + model[1,1]**2)
                dist = np.min([dist_end, dist_start])

            #if the distance
            if dist < min_dist:
                min_dist = dist
                min_dist_point = point

        def angle_to_point(point):
            if point[0] == 0:
                if point[1] > 0:
                    return np.pi/2
                else:
                    if point[1] < 0:
                        return -np.pi/2
                    else:
                        #print("IN COLLISION")
                        return 0
            if point[0] < 0:
                a = np.pi + np.arctan(point[1] / point[0])
                if a > np.pi:
                    a = -2 * np.pi + a
                return a
            return np.arctan(point[1]/point[0])

        rmsg = Polar_dist()
        rmsg.dist = min_dist
        rmsg.angle = angle_to_point(min_dist_point)
        self.publisher.publish(rmsg)


        print("Find point took: ", time()-start_time)
        start_time = time()

        cv.imwrite(f'/home/jousager/scan_pic/scan_{self.num:03d}.png', self.image)
        print(f'Writing image: {self.num}')
        self.num += 1

        print("Saving took: ", time()-start_time)
        start_time = time()
        #cv.imshow('image', self.image)
        #cv.waitKey(1)

    def draw_points(self, points):
        for point in points:
            try:
                cx = np.int(np.round(self.image.shape[0]/2 + self.drawScale * point[0]))
                cy = np.int(np.round(self.image.shape[1]/2 - self.drawScale * point[1]))
                #self.image[cx, cy] = (0, 0, 255)
                cv.circle(self.image, (cx, cy), 0, (0, 0, 255))
        #  x, -y
            except:
                print("Point draw err ", point)
        cv.arrowedLine(self.image, (self.image.shape[0]//2-2, self.image.shape[1]//2), (self.image.shape[0]//2+2, self.image.shape[1]//2), (0, 255, 0))

    def draw_lines(self, lines, inliers):
        colors = [(255, 0, 0), (0, 255, 0), (255, 255, 0), (255, 0, 255), (255, 255, 255), (0, 255, 255)]
        ci = 0
        for line, points in zip(lines, inliers):
            color = colors[ci]
            ci += 1
            ci = ci % len(colors)
            #print(ci)
            for point in points:
                cx = np.int(np.round(self.image.shape[0]/2 + self.drawScale * point[0]))
                cy = np.int(np.round(self.image.shape[1]/2 - self.drawScale * point[1]))
                cv.circle(self.image, (cx, cy), 0, color)
            sx = np.int(np.round(self.image.shape[0]/2 + self.drawScale * line[0, 0]))
            sy = np.int(np.round(self.image.shape[0]/2 - self.drawScale * line[0, 1]))
            ex = np.int(np.round(self.image.shape[0]/2 + self.drawScale * line[1, 0]))
            ey = np.int(np.round(self.image.shape[0]/2 - self.drawScale * line[1, 1]))
            cv.line(self.image, (sx, sy), (ex, ey), color)
def main(args=None):
    RANSAC_node = RANSAC_subscriber()
    rospy.spin()


if __name__ == '__main__':
    main()